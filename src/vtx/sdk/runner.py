"""
Runner — the orchestration core of the VTX Agentic SDK.

Three entry points:

* :meth:`Runner.run` — async, returns a :class:`RunResult`.
* :meth:`Runner.run_sync` — sync wrapper around ``run``.
* :meth:`Runner.run_streamed` — yields typed events as the run progresses.

Each variant drives the underlying ``vtx.turn.run_single_turn`` machinery
and adds SDK-level concerns: input/output guardrails, handoffs, tool
guardrails, approvals, structured output, and session persistence.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ..core.types import (
    AssistantMessage,
    Message,
    StopReason,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
)
from ..events import (
    ErrorEvent,
    TextDeltaEvent,
    ToolEndEvent,
    ToolResultEvent,
    ToolStartEvent,
    TurnEndEvent,
)
from ..turn import run_single_turn
from .agent import Agent
from .approvals import RunState, ToolApprovalItem
from .guardrails import run_input_guardrails, run_output_guardrails
from .items import (
    HandoffCallItem,
    HandoffOutputItem,
    MessageOutputItem,
    RunItem,
    ToolCallItem,
    ToolCallOutputItem,
)
from .results import RunResult
from .run_config import RunConfig
from .sessions import InMemorySession, Session
from .tracing import Trace, current_trace, is_tracing_disabled, span


def _new_session_if_missing(session: Session | None) -> Session:
    if session is None:
        return InMemorySession()
    return session


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _text_from_assistant(message: AssistantMessage) -> str:
    parts: list[str] = []
    for part in message.content:
        if isinstance(part, TextContent):
            parts.append(part.text)
        elif isinstance(part, ThinkingContent):
            parts.append(part.thinking)
    return "".join(parts)


def _to_input_items(messages: list[Message]) -> list[dict[str, Any]]:
    """Translate Vtx ``Message`` objects to the flat input-item dicts used
    by :class:`Session` and the structured output / handoff pipelines."""
    items: list[dict[str, Any]] = []
    for message in messages:
        if hasattr(message, "model_dump"):
            items.append(message.model_dump(exclude_none=True))
        else:
            items.append(dict(message))
    return items


def _from_input_items(items: list[dict[str, Any]]) -> list[Message]:
    """Inverse of :func:`_to_input_items`."""
    from ..core.types import (
        AssistantMessage,
        TextContent,
        ThinkingContent,
        ToolCall,
        ToolResultMessage,
        UserMessage,
    )

    out: list[Message] = []
    for item in items:
        role = item.get("role")
        if role == "user":
            content = item.get("content", "")
            if isinstance(content, str):
                out.append(UserMessage(content=content))
            else:
                out.append(UserMessage(content=content))
        elif role == "assistant":
            parts: list[Any] = []
            for part in item.get("content", []) or []:
                if isinstance(part, dict):
                    ptype = part.get("type")
                    if ptype == "text":
                        parts.append(TextContent(text=part.get("text", "")))
                    elif ptype in ("thinking", "reasoning"):
                        parts.append(
                            ThinkingContent(
                                thinking=part.get("thinking") or part.get("text") or "",
                                signature=part.get("signature"),
                            )
                        )
                    elif ptype == "tool_call":
                        parts.append(
                            ToolCall(
                                id=part.get("id", _new_id("call")),
                                name=part.get("name", ""),
                                arguments=part.get("arguments", {}),
                            )
                        )
                    else:
                        parts.append(part)
            for tc in item.get("tool_calls") or []:
                parts.append(
                    ToolCall(
                        id=tc.get("id", _new_id("call")),
                        name=tc.get("name", ""),
                        arguments=tc.get("arguments", {}),
                    )
                )
            out.append(AssistantMessage(content=parts))
        elif role in ("tool", "tool_result"):
            out.append(
                ToolResultMessage(
                    tool_call_id=item.get("tool_call_id", ""),
                    tool_name=item.get("tool_name", ""),
                    content=[TextContent(text=str(item.get("content", "")))],
                    is_error=item.get("is_error", False),
                )
            )
    return out


def _merge_history_and_input(
    history_items: list[dict[str, Any]], new_input: str | list[Any]
) -> list[dict[str, Any]]:
    """Build the input-item list for the first LLM call.

    Honors ``RunConfig.session_input_callback`` if set.
    """
    if isinstance(new_input, str):
        new_items: list[dict[str, Any]] = [{"role": "user", "content": new_input}]
    else:
        new_items = [dict(i) if isinstance(i, dict) else i for i in new_input]
    return list(history_items) + new_items


@dataclass
class _TurnOutcome:
    assistant: AssistantMessage | None
    tool_results: list[ToolResultMessage]
    stop_reason: StopReason
    sdk_items: list[RunItem] = field(default_factory=list)
    """Items to append to the result's ``new_items`` list."""


# ---------------------------------------------------------------------------
# Streamed event types - yielded by Runner.run_streamed
# ---------------------------------------------------------------------------


@dataclass
class _AgentStartEvent:
    agent_name: str = ""


@dataclass
class _TextDelta:
    delta: str = ""


@dataclass
class _ToolCallStart:
    tool_call_id: str = ""
    tool_name: str = ""


@dataclass
class _ToolCallEnd:
    tool_call_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class _ToolResult:
    tool_call_id: str = ""
    tool_name: str = ""
    output: Any = None
    is_error: bool = False


@dataclass
class _RunFinished:
    """Sentinel event: emitted at the end of a streamed run, carrying the final result."""

    result: RunResult


@dataclass
class RunStreamed:
    """Async iterable wrapper around a streamed run.

    Iterate over the object to receive typed events. After the iterator
    is exhausted, ``result`` holds the final :class:`RunResult`.
    """

    _events: AsyncIterator[Any]
    _result: RunResult | None = None
    _done: bool = False

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[Any]:
        try:
            async for event in self._events:
                if isinstance(event, _RunFinished):
                    self._result = event.result
                    continue
                yield event
        finally:
            self._done = True

    @property
    def result(self) -> RunResult:
        if self._result is None:
            raise RuntimeError(
                "RunStreamed.result is only available after the iterator is "
                "exhausted. Iterate the RunStreamed fully (use 'async for ... "
                "in streamed') before reading .result."
            )
        return self._result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class Runner:
    """Orchestrates a single run of one or more :class:`Agent` instances."""

    @staticmethod
    async def run(
        starting_agent: Agent,
        input: str | list[Any],
        *,
        session: Session | None = None,
        run_config: RunConfig | None = None,
        max_turns: int | None = None,
        cancellation: asyncio.Event | None = None,
        context: Any = None,
    ) -> RunResult:
        """Run ``starting_agent`` on ``input`` and return a :class:`RunResult`."""
        cfg = run_config or RunConfig()
        session_obj = _new_session_if_missing(session)
        max_t = max_turns or cfg.max_turns or 50

        # Run inside a top-level trace.
        trace_name = f"Agent run: {starting_agent.name}"
        with contextlib.ExitStack() as stack:
            if not is_tracing_disabled() and current_trace() is None:
                stack.enter_context(Trace(name=trace_name))
            with span("agent_run", agent_name=starting_agent.name):
                return await Runner._run_impl(
                    starting_agent, input, session_obj, cfg, max_t, cancellation, context
                )

    @staticmethod
    def run_sync(
        starting_agent: Agent,
        input: str | list[Any],
        *,
        session: Session | None = None,
        run_config: RunConfig | None = None,
        max_turns: int | None = None,
        cancellation: asyncio.Event | None = None,
        context: Any = None,
    ) -> RunResult:
        """Synchronous wrapper around :meth:`run`.

        Runs the coroutine on a dedicated worker thread so it is safe to
        call from inside a running event loop.
        """
        import concurrent.futures

        def _runner() -> RunResult:
            return asyncio.run(
                Runner.run(
                    starting_agent,
                    input,
                    session=session,
                    run_config=run_config,
                    max_turns=max_turns,
                    cancellation=cancellation,
                    context=context,
                )
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_runner).result()

    @staticmethod
    def run_streamed(
        starting_agent: Agent,
        input: str | list[Any],
        *,
        session: Session | None = None,
        run_config: RunConfig | None = None,
        max_turns: int | None = None,
        cancellation: asyncio.Event | None = None,
        context: Any = None,
    ) -> RunStreamed:
        """Stream events from a run. Iterate the returned object; ``.result``
        is available after iteration completes."""

        async def _event_iter() -> AsyncIterator[Any]:
            cfg = run_config or RunConfig()
            session_obj = _new_session_if_missing(session)
            max_t = max_turns or cfg.max_turns or 50
            with contextlib.ExitStack() as stack:
                if not is_tracing_disabled() and current_trace() is None:
                    stack.enter_context(Trace(name=f"Agent run: {starting_agent.name}"))
                with span("agent_run", agent_name=starting_agent.name):
                    yield _AgentStartEvent(agent_name=starting_agent.name)
                    result = await Runner._run_impl(
                        starting_agent,
                        input,
                        session_obj,
                        cfg,
                        max_t,
                        cancellation,
                        context,
                        yield_events=True,
                    )
                    for ev in result._streamed_events:
                        yield ev
                    yield _RunFinished(result=result)

        return RunStreamed(_event_iter())

    # ------------------------------------------------------------------
    # Implementation
    # ------------------------------------------------------------------

    @staticmethod
    async def _run_impl(
        starting_agent: Agent,
        input: str | list[Any],
        session: Session,
        cfg: RunConfig,
        max_turns: int,
        cancellation: asyncio.Event | None,
        context: Any,
        yield_events: bool = False,
    ) -> RunResult:
        sdk_items: list[RunItem] = []
        streamed_events: list[Any] = []
        total_usage = Usage()

        # 1. Apply input guardrails (against the raw input + starting agent).
        from .guardrails.types import _InputGuardrailData

        if starting_agent.input_guardrails:
            input_text = input if isinstance(input, str) else json.dumps(input, default=str)
            data = _InputGuardrailData(context=context, agent=starting_agent, input=input_text)
            await run_input_guardrails(starting_agent.input_guardrails, data)

        # 2. Load history from the session.
        history_items = await session.get_items(
            limit=cfg.session_settings.limit if cfg.session_settings else None
        )
        if cfg.session_input_callback is not None:
            new_input_items = (
                input if isinstance(input, list) else [{"role": "user", "content": input}]
            )
            full_input = cfg.session_input_callback(history_items, new_input_items)
        else:
            full_input = _merge_history_and_input(history_items, input)

        # Persist the new input to the session.
        new_input_items = (
            input if isinstance(input, list) else [{"role": "user", "content": input}]
        )
        await session.add_items(new_input_items)

        # 3. Resolve the active agent, its tools, and its system prompt.
        active_agent: Agent = starting_agent
        # Build per-agent system prompt and tools cache.
        system_prompt = active_agent.build_system_prompt(context=context)
        all_tools, handoff_targets, handoff_tools_by_name = active_agent.all_tools()
        provider = active_agent.resolve_provider()

        messages: list[Message] = _from_input_items(full_input)

        current_active_agent: Agent = active_agent
        current_system_prompt = system_prompt
        current_provider = provider
        current_all_tools = all_tools
        current_handoff_targets = handoff_targets
        # handoff_tools_by_name is the third element of all_tools(); kept
        # around for future use but the current loop reads the targets.
        _current_handoff_tools_by_name = handoff_tools_by_name

        # 4. Loop.
        stop_reason = StopReason.STOP
        pending_approvals: list[ToolApprovalItem] = []
        for _turn_idx in range(max_turns):
            if cancellation is not None and cancellation.is_set():
                stop_reason = StopReason.INTERRUPTED
                break

            # Run a single turn.
            outcome = await Runner._run_single_turn_sdk(
                agent=current_active_agent,
                messages=messages,
                tools=current_all_tools,
                handoff_by_name=current_handoff_targets,
                system_prompt=current_system_prompt,
                provider=current_provider,
                cancellation=cancellation,
                yield_events=yield_events,
                streamed_events=streamed_events,
                sdk_items=sdk_items,
                pending_approvals=pending_approvals,
                session=session,
            )
            total_usage = _accumulate_usage(total_usage, outcome.assistant)

            if outcome.assistant is not None:
                messages.append(outcome.assistant)
                await session.add_items(_to_input_items([outcome.assistant]))
            for result_msg in outcome.tool_results:
                messages.append(result_msg)
                await session.add_items(_to_input_items([result_msg]))

            stop_reason = outcome.stop_reason

            # 5. Handle handoff: if any tool call in the last assistant
            #    message was a handoff, switch the active agent.
            switched = await Runner._maybe_handoff(
                outcome, current_active_agent, messages, context, streamed_events, sdk_items
            )
            if switched is not None:
                (
                    current_active_agent,
                    current_system_prompt,
                    current_provider,
                    current_all_tools,
                    current_handoff_targets,
                    _current_handoff_tools_by_name,
                ) = switched

            if pending_approvals:
                # Pause for human approval.
                return RunResult(
                    final_output=None,
                    new_items=sdk_items,
                    interruptions=pending_approvals,
                    state=RunState(
                        original_input=input,
                        pending_tool_calls=[
                            ToolCall(
                                id=item.call_id, name=item.tool_name, arguments=item.arguments
                            )
                            for item in pending_approvals
                        ],
                        new_items=sdk_items,
                    ),
                    stop_reason=StopReason.TOOL_USE,
                    usage=total_usage,
                    agent_name=current_active_agent.name,
                )

            if stop_reason != StopReason.TOOL_USE:
                break

        # 5. Apply output guardrails.
        final_text = (
            _text_from_assistant(messages[-1])
            if messages and isinstance(messages[-1], AssistantMessage)
            else ""
        )
        if current_active_agent.output_guardrails:
            from .guardrails.types import _OutputGuardrailData

            data = _OutputGuardrailData(
                context=context, agent=current_active_agent, output=final_text
            )
            await run_output_guardrails(current_active_agent.output_guardrails, data)

        # 6. Final output.
        final_output: Any = final_text
        if current_active_agent.output_type is not None:
            schema = current_active_agent.output_type  # alias
            try:
                final_output = (
                    schema.model_validate_json(final_text)
                    if final_text
                    else schema.model_construct()
                )
            except Exception:
                # Fallback: try plain JSON.
                try:
                    final_output = schema.model_validate(json.loads(final_text))
                except Exception:
                    final_output = schema.model_construct()

        result = RunResult(
            final_output=final_output,
            new_items=sdk_items,
            stop_reason=stop_reason,
            usage=total_usage,
            agent_name=current_active_agent.name,
        )
        if yield_events:
            result._streamed_events = streamed_events
        return result

    # ------------------------------------------------------------------
    # Single turn
    # ------------------------------------------------------------------

    @staticmethod
    async def _run_single_turn_sdk(
        *,
        agent: Agent,
        messages: list[Message],
        tools: list[BaseTool],  # type: ignore[name-defined]
        handoff_by_name: dict[str, Agent],
        system_prompt: str,
        provider: Any,
        cancellation: asyncio.Event | None,
        yield_events: bool,
        streamed_events: list[Any],
        sdk_items: list[RunItem],
        pending_approvals: list[ToolApprovalItem],
        session: Session,
    ) -> _TurnOutcome:
        from ..tools import get_tool_definitions

        get_tool_definitions(tools) if tools else None
        tool_call_count = 0
        tool_results: list[ToolResultMessage] = []
        text_chunks: list[str] = []
        thinking_chunks: list[str] = []
        signature: str | None = None
        sdk_tool_calls: list[ToolCall] = []
        stop_reason: StopReason = StopReason.STOP

        from ..events import ThinkingDeltaEvent

        async for event in run_single_turn(
            provider=provider,
            messages=messages,
            tools=tools,
            system_prompt=system_prompt,
            turn=1,
            cancel_event=cancellation,
        ):
            if isinstance(event, TextDeltaEvent):
                text_chunks.append(event.delta)
                if yield_events:
                    streamed_events.append(_TextDelta(delta=event.delta))
            elif isinstance(event, ThinkingDeltaEvent):
                thinking_chunks.append(event.delta)
            elif isinstance(event, ToolStartEvent):
                tool_call_count += 1
                if yield_events:
                    streamed_events.append(
                        _ToolCallStart(tool_call_id=event.tool_call_id, tool_name=event.tool_name)
                    )
            elif isinstance(event, ToolEndEvent):
                if yield_events:
                    streamed_events.append(
                        _ToolCallEnd(
                            tool_call_id=event.tool_call_id,
                            tool_name=event.tool_name,
                            arguments=event.arguments,
                        )
                    )
            elif isinstance(event, ToolResultEvent):
                if event.result is not None:
                    tool_results.append(event.result)
                    if yield_events:
                        streamed_events.append(
                            _ToolResult(
                                tool_call_id=event.tool_call_id,
                                tool_name=event.tool_name,
                                output=_text_of_tool_result(event.result),
                                is_error=event.result.is_error,
                            )
                        )
            elif isinstance(event, TurnEndEvent):
                stop_reason = event.stop_reason
                if event.assistant_message is not None:
                    sdk_tool_calls = [
                        c for c in event.assistant_message.content if isinstance(c, ToolCall)
                    ]
            elif isinstance(event, ErrorEvent):
                stop_reason = StopReason.ERROR

        # Build the AssistantMessage from accumulated parts.
        content_parts: list[Any] = []
        if thinking_chunks:
            content_parts.append(
                ThinkingContent(thinking="".join(thinking_chunks), signature=signature)
            )
        if text_chunks:
            content_parts.append(TextContent(text="".join(text_chunks)))
        for tc in sdk_tool_calls:
            content_parts.append(tc)

        assistant = AssistantMessage(content=content_parts, stop_reason=stop_reason)

        # Build SDK items.
        if thinking_chunks:
            from .items import ReasoningItem

            sdk_items.append(
                ReasoningItem(
                    agent=agent,
                    raw_item=ThinkingContent(
                        thinking="".join(thinking_chunks), signature=signature
                    ),
                )
            )
        if text_chunks:
            sdk_items.append(MessageOutputItem(agent=agent, raw_item=assistant))
        for tc in sdk_tool_calls:
            is_handoff = tc.name in handoff_by_name
            if is_handoff:
                sdk_items.append(
                    HandoffCallItem(
                        agent=agent, raw_item=tc, target_agent_name=handoff_by_name[tc.name].name
                    )
                )
            else:
                sdk_items.append(ToolCallItem(agent=agent, raw_item=tc, tool_name=tc.name))
        for tr in tool_results:
            sdk_items.append(
                ToolCallOutputItem(agent=agent, raw_item=tr, output=_text_of_tool_result(tr))
            )

        return _TurnOutcome(
            assistant=assistant, tool_results=tool_results, stop_reason=stop_reason, sdk_items=[]
        )

    @staticmethod
    async def _maybe_handoff(
        outcome: _TurnOutcome,
        current_agent: Agent,
        messages: list[Message],
        context: Any,
        streamed_events: list[Any],
        sdk_items: list[RunItem],
    ) -> tuple[Agent, str, Any, list[BaseTool], dict[str, Agent], dict[str, BaseTool]] | None:  # type: ignore[name-defined]
        """If the last assistant turn called a handoff, switch active agent."""
        from ..core.types import ToolCall

        if not outcome.assistant:
            return None
        handoff_calls = [
            c
            for c in outcome.assistant.content
            if isinstance(c, ToolCall) and c.name.startswith("transfer_to_")
        ]
        if not handoff_calls:
            return None

        # The handoff tool's result is the target agent's name; switch.
        # Find the matching handoff tool name.
        _all_tools, handoff_targets, _handoff_by_name = current_agent.all_tools()
        target_name = None
        for tc in handoff_calls:
            for htool_name, target in handoff_targets.items():
                if tc.name == htool_name:
                    target_name = target.name
                    target_agent = target
                    break
            if target_name:
                break

        if target_name is None:
            return None

        # The handoff target takes over the conversation. We add a
        # synthetic user note that names the handoff so the target agent
        # has a clean entry point.
        from ..core.types import UserMessage

        messages.append(
            UserMessage(
                content=(
                    f"[handoff from {current_agent.name} → {target_agent.name}. "
                    "Take over the conversation from here.]"
                )
            )
        )

        # Build the new system prompt / provider / tools for the target.

        new_tools, new_targets, new_by_name = target_agent.all_tools()
        new_system_prompt = target_agent.build_system_prompt(context=context)
        new_provider = target_agent.resolve_provider()

        sdk_items.append(
            HandoffOutputItem(
                agent=current_agent,
                raw_item={"role": "assistant", "content": f"Handoff to {target_agent.name}."},
                source_agent=current_agent,
                target_agent=target_agent,
            )
        )

        return (target_agent, new_system_prompt, new_provider, new_tools, new_targets, new_by_name)


def _accumulate_usage(total: Usage, assistant: AssistantMessage | None) -> Usage:
    if assistant is None or assistant.usage is None:
        return total
    total.input_tokens += assistant.usage.input_tokens
    total.output_tokens += assistant.usage.output_tokens
    total.cache_read_tokens += assistant.usage.cache_read_tokens
    total.cache_write_tokens += assistant.usage.cache_write_tokens
    return total


def _text_of_tool_result(result: ToolResultMessage) -> str:
    parts: list[str] = []
    for part in result.content:
        if isinstance(part, TextContent):
            parts.append(part.text)
    return "".join(parts) if parts else ("(no output)" if not result.is_error else "(error)")


# Re-export BaseTool for type hints inside the function.
from ..tools.base import BaseTool  # noqa: E402

__all__ = ["RunStreamed", "Runner"]
