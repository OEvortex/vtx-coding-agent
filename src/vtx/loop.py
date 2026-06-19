"""
Run the agent loop and stream events for the UI.

Each turn runs `run_single_turn()`, forwards turn/tool events immediately, persists assistant/tool
messages to the session, and decides whether to continue. After every turn, overflow compaction
may run and emit its own start/end events so the UI can reflect that state in real time.

The loop ends on stop/error/interruption, compaction pause mode, or max turns.
"""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from . import config as vtx_config
from .context import Context
from .core.compaction import generate_summary, is_overflow
from .core.errors import format_error
from .core.types import (
    AssistantMessage,
    ImageContent,
    Message,
    StopReason,
    TextContent,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from .events import (
    AgentEndEvent,
    AgentStartEvent,
    BackgroundTaskCompletedEvent,
    CompactionEndEvent,
    CompactionStartEvent,
    ErrorEvent,
    Event,
    InterruptedEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from .extensions import (
    AGENT_END,
    AGENT_START,
    COMPACTION_END,
    COMPACTION_START,
    TURN_END,
    TURN_START,
    EventBus,
)
from .llm import BaseProvider
from .prompts import build_system_prompt
from .session import MessageEntry, Session
from .tools import BaseTool
from .turn import run_single_turn

# Re-exported so existing callers (runtime, tests) keep working.
__all__ = ["Agent", "AgentConfig", "build_system_prompt"]

log = logging.getLogger("vtx.loop")


@dataclass
class AgentConfig:
    context_window: int | None = None
    max_output_tokens: int | None = None


class Agent:
    def __init__(
        self,
        provider: BaseProvider,
        tools: list[BaseTool],
        session: Session,
        cwd: str | None = None,
        context: Context | None = None,
        system_prompt: str | None = None,
        config: AgentConfig | None = None,
        extensions: EventBus | None = None,
        background_manager: Any = None,
    ):
        self.provider = provider
        self.tools = tools
        self.session = session
        self.config = config or AgentConfig()
        self._cwd = cwd or os.getcwd()
        self._context = context or Context.load(self._cwd)
        self._system_prompt = system_prompt or build_system_prompt(
            self._cwd, self._context, tools=tools
        )
        self._extensions = extensions
        self._run_usage = Usage()
        self._background_manager = background_manager

    @property
    def context(self) -> Context:
        return self._context

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def reload_context(self) -> None:
        self._context = Context.load(self._cwd)
        self._system_prompt = build_system_prompt(self._cwd, self._context, tools=self.tools)

    @property
    def messages(self) -> list[Message]:
        return self.session.messages

    def _add_usage(self, usage: Usage | None) -> None:
        if usage:
            self._run_usage.input_tokens += usage.input_tokens
            self._run_usage.output_tokens += usage.output_tokens
            self._run_usage.cache_read_tokens += usage.cache_read_tokens
            self._run_usage.cache_write_tokens += usage.cache_write_tokens

    async def run(
        self,
        query: str,
        images: list[ImageContent] | None = None,
        cancel_event: asyncio.Event | None = None,
        steer_event: asyncio.Event | None = None,
    ) -> AsyncIterator[Event]:
        self._run_usage = Usage()

        if images:
            user_content: list[TextContent | ImageContent] = [TextContent(text=query), *images]
            user_message = UserMessage(content=user_content)
        else:
            user_message = UserMessage(content=query)

        self.session.append_message(user_message)

        if self._extensions is not None:
            await self._extensions.emit(AGENT_START, cancel_event=cancel_event)

        yield AgentStartEvent()

        turn = 0
        stop_reason = StopReason.STOP
        was_interrupted = False

        system_prompt = self._system_prompt

        try:
            max_turns = vtx_config.agent.max_turns
            while turn < max_turns:
                if cancel_event and cancel_event.is_set():
                    was_interrupted = True
                    stop_reason = StopReason.INTERRUPTED
                    yield InterruptedEvent(message="Interrupted by user")
                    break

                if steer_event and steer_event.is_set():
                    stop_reason = StopReason.STEER
                    break

                turn += 1
                yield TurnStartEvent(turn=turn)

                if self._extensions is not None:
                    await self._extensions.emit(TURN_START, cancel_event=cancel_event, turn=turn)

                messages = self.session.messages
                tool_results: list[ToolResultMessage] = []
                async for event in run_single_turn(
                    provider=self.provider,
                    messages=messages,
                    tools=self.tools,
                    system_prompt=system_prompt,
                    turn=turn,
                    cancel_event=cancel_event,
                    extensions=self._extensions,
                ):
                    yield event

                    if isinstance(event, TurnEndEvent):
                        if event.assistant_message:
                            self._add_usage(event.assistant_message.usage)
                            self.session.append_message(event.assistant_message)
                        tool_results = event.tool_results
                        stop_reason = event.stop_reason
                        for result in tool_results:
                            self.session.append_message(result)
                    elif isinstance(event, InterruptedEvent):
                        was_interrupted = True

                if self._extensions is not None:
                    await self._extensions.emit(
                        TURN_END, cancel_event=cancel_event, turn=turn, tool_results=tool_results
                    )

                # Drain background-task completions and inject a synthetic
                # message into the next turn so the model sees the
                # notification. Done between turns (not mid-turn) so we
                # never interrupt an in-flight stream. ``drain_completed``
                # flips each record's ``notified`` flag, so each task is
                # delivered at most once.
                for evt in self._drain_background_notifications():
                    yield evt

                if was_interrupted or stop_reason == StopReason.INTERRUPTED:
                    stop_reason = StopReason.INTERRUPTED
                    break

                if steer_event and steer_event.is_set():
                    stop_reason = StopReason.STEER
                    break

                # Check for context overflow after each turn.
                # We iterate events instead of awaiting a single compaction result so
                # CompactionStartEvent can be forwarded immediately and the UI can
                # render a "compacting" state while summary generation is running.
                did_compact = False
                async for compaction_event in self._check_compaction(
                    stop_reason, system_prompt, cancel_event
                ):
                    yield compaction_event
                    if isinstance(compaction_event, CompactionEndEvent):
                        did_compact = True
                if did_compact:
                    if vtx_config.compaction.on_overflow == "pause":
                        break
                    # Continue mode: synthetic user message was injected, continue loop
                    continue

                if stop_reason != StopReason.TOOL_USE:
                    break

            if turn >= max_turns and not was_interrupted and stop_reason == StopReason.TOOL_USE:
                stop_reason = StopReason.LENGTH

        except Exception as e:  # intentionally broad — top-level boundary; crash = broken TUI
            yield ErrorEvent(error=format_error(e))
            stop_reason = StopReason.ERROR

        yield AgentEndEvent(stop_reason=stop_reason, total_turns=turn, total_usage=self._run_usage)

        # Final drain in case a background task completed during the very
        # last turn. We yield both the structured event and the synthetic
        # message; the renderer is responsible for surface rendering.
        for evt in self._drain_background_notifications():
            yield evt

        if self._extensions is not None:
            await self._extensions.emit(
                AGENT_END,
                cancel_event=cancel_event,
                stop_reason=stop_reason,
                total_turns=turn,
                total_usage=self._run_usage,
            )

    def _drain_background_notifications(self) -> list[Event]:
        """Pull finished background tasks from the manager.

        Returns a list containing, for each newly-finished task:
        - one :class:`BackgroundTaskCompletedEvent` for the UI, and
        - one synthetic :class:`UserMessage` already appended to the
          session so the model sees it on the next turn.

        The synthetic message is wrapped in a marker tag
        (``vtx:background-task-completion``) and the system prompt
        instructs the model to treat it as a system event, not a user
        instruction (anthropics/claude-code#35610).

        ``drain_completed`` flips ``notified=True`` on each record
        before returning, so this list contains each task exactly
        once even if the parent does nothing in response
        (anthropics/claude-code#20679).
        """
        from .tools.background import BACKGROUND_NOTIFICATION_TAG

        if self._background_manager is None:
            return []

        out: list[Event] = []
        try:
            drained = self._background_manager.drain_completed()
        except Exception:
            log.exception("BackgroundTaskManager.drain_completed failed")
            return []

        for record in drained:
            summary = self._format_bg_summary(record)
            out.append(
                BackgroundTaskCompletedEvent(
                    task_id=record.task_id,
                    description=record.description,
                    subagent_type=record.subagent_type,
                    status=record.status,  # type: ignore[arg-type]
                    summary=summary,
                    turns=record.turns,
                    total_tokens=record.total_tokens,
                    notification_tag=BACKGROUND_NOTIFICATION_TAG,
                )
            )
            synthetic = UserMessage(
                content=(
                    f"<{BACKGROUND_NOTIFICATION_TAG}> "
                    f"Background task '{record.description}' "
                    f"({record.subagent_type}) finished with status "
                    f"{record.status} in {record.turns} turn(s).\n\n"
                    f"task_id={record.task_id}\n\n"
                    f"Final answer:\n{record.result_text or '(no result)'}"
                    f"</{BACKGROUND_NOTIFICATION_TAG}>"
                )
            )
            self.session.append_message(synthetic)
        return out

    @staticmethod
    def _format_bg_summary(record: Any) -> str:
        head = record.result_text or ""
        head = head.strip().splitlines()
        if head:
            first = head[0].strip()
            if len(first) > 160:
                first = first[:157] + "..."
            return first
        if record.error:
            return f"error: {record.error}"
        return "(no result)"

    async def _check_compaction(
        self, stop_reason: StopReason, system_prompt: str, cancel_event: asyncio.Event | None
    ) -> AsyncIterator[CompactionStartEvent | CompactionEndEvent]:
        if stop_reason == StopReason.ERROR:
            return

        # Get the latest assistant message that has usage.
        # The most recent assistant entry can be interrupted/error and have no usage.
        last_usage: Usage | None = None
        for entry in reversed(self.session.active_entries):
            if isinstance(entry, MessageEntry) and isinstance(entry.message, AssistantMessage):
                usage = entry.message.usage
                if usage is None:
                    continue
                last_usage = usage
                break

        if last_usage is None:
            return

        context_window = self.config.context_window or vtx_config.agent.default_context_window
        threshold_percent = vtx_config.compaction.threshold_percent

        if not is_overflow(last_usage, context_window, threshold_percent):
            return

        if cancel_event and cancel_event.is_set():
            return

        tokens_before = (
            last_usage.input_tokens
            + last_usage.output_tokens
            + last_usage.cache_read_tokens
            + last_usage.cache_write_tokens
        )

        # Yield start event immediately so UI can show status
        yield CompactionStartEvent()

        if self._extensions is not None:
            await self._extensions.emit(
                COMPACTION_START, cancel_event=cancel_event, tokens_before=tokens_before
            )

        try:
            # Use all_messages (uncompacted) for summarization so LLM sees full history
            summary = await generate_summary(
                self.session.all_messages, self.provider, system_prompt
            )

            # Everything before is summarized, nothing "kept"
            first_kept_id = self.session.leaf_id or ""

            self.session.append_compaction(
                summary=summary, first_kept_entry_id=first_kept_id, tokens_before=tokens_before
            )

            # In continue mode, inject synthetic continue message
            if vtx_config.compaction.on_overflow == "continue":
                continue_msg = UserMessage(
                    content=(
                        "Continue if you have next steps, or stop and ask for clarification if you"
                        " are unsure how to proceed. If there is nothing to do don't add a large"
                        " preamble, just summarise everything so far in 2-3 lines and be done."
                    )
                )
                self.session.append_message(continue_msg)

            yield CompactionEndEvent(tokens_before=tokens_before)

            if self._extensions is not None:
                await self._extensions.emit(
                    COMPACTION_END,
                    cancel_event=cancel_event,
                    tokens_before=tokens_before,
                    aborted=False,
                )

        except Exception as e:
            yield CompactionEndEvent(
                tokens_before=tokens_before, aborted=True, reason=format_error(e)
            )
            if self._extensions is not None:
                await self._extensions.emit(
                    COMPACTION_END,
                    cancel_event=cancel_event,
                    tokens_before=tokens_before,
                    aborted=True,
                    reason=format_error(e),
                )
