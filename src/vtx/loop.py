"""
Run the agent loop and stream events for the UI.

Each turn runs `run_single_turn()`, forwards turn/tool events immediately, persists assistant/tool
messages to the session, and decides whether to continue. After every turn, overflow compaction
may run and emit its own start/end events so the UI can reflect that state in real time.

When a :class:`~vtx.goal.Goal` is active, the loop invokes an evaluator between turns to
decide whether the completion condition is met; "yes" ends the run, "no" injects a synthetic
guidance message and starts another turn.

The loop ends on stop/error/interruption, compaction pause mode, max turns, or goal-resolution
(achieved / budget-limited).
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
    GoalAchievedEvent,
    GoalBudgetLimitedEvent,
    GoalContinueEvent,
    GoalEvaluatingEvent,
    GoalStartEvent,
    InterruptedEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from .extensions import (
    AGENT_END,
    AGENT_START,
    COMPACTION_END,
    COMPACTION_START,
    GOAL_END,
    GOAL_START,
    TURN_END,
    TURN_START,
    EventBus,
)
from .goal import GOAL_TAG, GoalManager
from .llm import BaseProvider
from .prompts import build_system_prompt
from .session import MessageEntry, Session
from .tools import BaseTool
from .turn import run_single_turn

# Re-exported so existing callers (runtime, tests) keep working.
__all__ = ["Agent", "AgentConfig", "GoalManager", "build_system_prompt"]

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
        goal_manager: GoalManager | None = None,
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
        self._goal_manager = goal_manager or GoalManager(
            max_objective_chars=vtx_config.goal.max_objective_chars,
            max_turns_default=vtx_config.goal.max_turns,
        )

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
        goal_status_at_end: str | None = None
        goal_reason_at_end: str | None = None

        # Emit a GoalStartEvent up front so the UI can show the badge
        # immediately rather than waiting for the first evaluator call.
        if self._goal_manager.goal is not None and self._goal_manager.goal.status == "pursuing":
            yield GoalStartEvent(
                objective=self._goal_manager.goal.objective,
                max_turns_override=self._goal_manager.goal.max_turns_override,
            )
            if self._extensions is not None:
                await self._extensions.emit(
                    GOAL_START, objective=self._goal_manager.goal.objective
                )

        system_prompt = self._system_prompt
        max_turns = self._effective_max_turns()

        try:
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
                            self._goal_manager.record_turn_tokens(
                                self._extract_tokens(event.assistant_message.usage)
                            )
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

                # Goal-mode evaluation happens after compaction and before
                # the regular "did the agent finish?" check. The evaluator
                # decides whether to inject a synthetic guidance user
                # message (and start another turn) or end the run. The
                # check must run even when ``stop_reason != TOOL_USE`` so
                # the agent gets one more chance to satisfy the goal.
                if (
                    self._goal_manager.goal is not None
                    and self._goal_manager.goal.status == "pursuing"
                ):
                    async for evt in self._evaluate_goal():
                        yield evt
                    goal = self._goal_manager.goal
                    if goal.status == "achieved":
                        goal_status_at_end = "achieved"
                        goal_reason_at_end = goal.last_reason
                        stop_reason = StopReason.STOP
                        break
                    if goal.status == "budget_limited":
                        goal_status_at_end = "budget_limited"
                        goal_reason_at_end = goal.last_reason
                        stop_reason = StopReason.LENGTH
                        break
                    # otherwise "pursuing" → loop continues with synthetic message

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

                # If a goal is still active, the loop should keep going
                # even when the agent's last turn had no tool calls. The
                # evaluator (above) has just had its say; without the
                # override below, a goal with ``stop_reason == STOP``
                # would exit silently.
                if (
                    self._goal_manager.goal is not None
                    and self._goal_manager.goal.status == "pursuing"
                ):
                    continue

                if stop_reason != StopReason.TOOL_USE:
                    break

            if turn >= max_turns and not was_interrupted:
                # Ran out of turns. If a goal is still active, prefer to
                # mark it budget_limited so the transcript clearly says
                # the goal didn't finish.
                if (
                    self._goal_manager.goal is not None
                    and self._goal_manager.goal.status == "pursuing"
                ):
                    self._goal_manager.mark_budget_limited()
                    goal_status_at_end = "budget_limited"
                    goal_reason_at_end = "turn cap reached"
                    yield GoalBudgetLimitedEvent(
                        turns_evaluated=self._goal_manager.goal.turns_evaluated,
                        tokens_used=self._goal_manager.goal.tokens_used,
                        max_turns=max_turns,
                    )
                stop_reason = (
                    StopReason.LENGTH if stop_reason == StopReason.TOOL_USE else stop_reason
                )

        except Exception as e:  # intentionally broad — top-level boundary; crash = broken TUI
            yield ErrorEvent(error=format_error(e))
            stop_reason = StopReason.ERROR

        # Persist the final goal snapshot so resume sees the right state.
        if self._goal_manager.goal is not None and self.session is not None:
            try:
                self.session.append_goal_state(self._goal_manager.to_entry_dict())
            except Exception:
                log.exception("failed to persist goal state")

        yield AgentEndEvent(
            stop_reason=stop_reason,
            total_turns=turn,
            total_usage=self._run_usage,
            goal_status=goal_status_at_end,
            goal_reason=goal_reason_at_end,
        )

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
                goal_status=goal_status_at_end,
                goal_reason=goal_reason_at_end,
            )

        if self._extensions is not None and goal_status_at_end in {"achieved", "budget_limited"}:
            await self._extensions.emit(GOAL_END, status=goal_status_at_end)

    def _effective_max_turns(self) -> int:
        """Combine global ``agent.max_turns`` with any goal override.

        Goal override is informational (the loop always terminates at
        ``agent.max_turns`` as a hard ceiling). When a goal is active
        we additionally bound the run by ``goal.max_turns``.
        """
        global_max = vtx_config.agent.max_turns
        if self._goal_manager.goal is None:
            return global_max
        override = self._goal_manager.goal.max_turns_override
        if override:
            return min(global_max, override)
        return min(global_max, self._goal_manager._max_turns_default)

    @staticmethod
    def _extract_tokens(usage: Usage | None) -> int:
        if usage is None:
            return 0
        return (
            (usage.input_tokens or 0)
            + (usage.output_tokens or 0)
            + (usage.cache_read_tokens or 0)
            + (usage.cache_write_tokens or 0)
        )

    async def _evaluate_goal(self) -> AsyncIterator[Event]:
        """Drive one evaluator decision and inject guidance on "no".

        Yields ``GoalEvaluatingEvent`` while the call is in flight,
        then either ``GoalAchievedEvent`` (loop ends) or
        ``GoalContinueEvent`` + a synthetic user message in the session.
        """
        if self._goal_manager.goal is None:
            return
        yield GoalEvaluatingEvent(turns_evaluated=self._goal_manager.goal.turns_evaluated)
        outcome = await self._goal_manager.evaluate(
            self.session.messages, provider=self.provider, model=self._resolve_evaluator_model()
        )
        goal = self._goal_manager.goal
        if outcome.achieved:
            yield GoalAchievedEvent(
                reason=outcome.reason,
                turns_evaluated=goal.turns_evaluated,
                tokens_used=goal.tokens_used,
            )
            return
        yield GoalContinueEvent(reason=outcome.reason, turns_evaluated=goal.turns_evaluated)
        guidance = (
            f"<{GOAL_TAG}>evaluator verdict: NOT YET — {outcome.reason}. "
            "Continue working toward the goal; do not declare completion. "
            "When the condition is genuinely satisfied, surface concrete evidence "
            "(test output, file paths, command exit codes) in your next turn.</"
            f"{GOAL_TAG}>"
        )
        self.session.append_message(UserMessage(content=guidance))

    def _resolve_evaluator_model(self) -> str:
        """Pick the model the evaluator call should use.

        Empty config means "use the active default"; non-empty overrides
        route the call to a different model. The provider's config is
        swapped briefly inside :meth:`GoalManager.evaluate` and restored.
        """
        cfg_model = vtx_config.goal.evaluator_model.strip()
        if cfg_model:
            return cfg_model
        return self.provider.config.model

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
