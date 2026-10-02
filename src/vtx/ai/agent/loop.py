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
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from vtx.ai import BaseProvider
from vtx.ai.agent.agent_runner import AgentRunSpec, run_agent_turn
from vtx.ai.agent.background import BACKGROUND_NOTIFICATION_TAG
from vtx.ai.agent.config import get_harness_config
from vtx.ai.agent.extensions import (
    AGENT_END,
    AGENT_SETTLED,
    AGENT_START,
    COMPACTION_END,
    COMPACTION_START,
    TURN_END,
    TURN_START,
    EventBus,
)
from vtx.ai.agent.session import CompactionEntry, MessageEntry, Session
from vtx.ai.agent.skills_refresh import SkillsCatalogState
from vtx.ai.agent.tools import BaseTool
from vtx.core.compaction import SummaryProgress, generate_summary, is_overflow
from vtx.core.errors import format_error
from vtx.core.events import (
    AgentEndEvent,
    AgentStartEvent,
    BackgroundTaskCompletedEvent,
    CompactionEndEvent,
    CompactionProgressEvent,
    CompactionStartEvent,
    ErrorEvent,
    Event,
    InterruptedEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from vtx.core.types import (
    AssistantMessage,
    ImageContent,
    Message,
    StopReason,
    TextContent,
    ToolResultMessage,
    Usage,
    UserMessage,
)

# Re-exported so existing callers (runtime, tests) keep working.
__all__ = ["Agent", "AgentConfig"]

log = logging.getLogger("agent.loop")

# How often the compaction generator wakes to forward buffered progress.
# Also the cooldown for progress events whose section count did not change.
COMPACTION_PROGRESS_INTERVAL = 0.25


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
        context: Any | None = None,
        system_prompt: str | None = None,
        config: AgentConfig | None = None,
        extensions: EventBus | None = None,
        background_manager: Any = None,
        hooks: list[Any] | None = None,
        context_loader: Any = None,
        prompt_builder: Any = None,
        depth: int = 0,
    ):
        """Create an agent engine.

        The harness is product-agnostic: ``context`` is an opaque object
        produced by ``context_loader(cwd)`` and rendered by
        ``prompt_builder(cwd, context, tools=tools)``. Products (e.g. the
        coding agent) inject both; without them only a static
        ``system_prompt`` is used.

        ``depth`` is the sub-agent nesting level (0 for a top-level session).
        Only depth 0 delivers the skills catalog refresh: a child shares the
        parent's session, so it would duplicate an announcement the parent
        already made.
        """
        self.provider = provider
        self.tools = tools
        self.session = session
        self.config = config or AgentConfig()
        self._cwd = cwd or os.getcwd()
        self._depth = depth
        self._context_loader = context_loader
        self._prompt_builder = prompt_builder
        if context is None and context_loader is not None:
            context = context_loader(self._cwd)
        self._context = context
        if system_prompt is None and prompt_builder is not None:
            system_prompt = prompt_builder(self._cwd, self._context, tools=tools)
        self._system_prompt = system_prompt or ""
        self._extensions = extensions
        self._run_usage = Usage()
        self._background_manager = background_manager
        # In-process hooks (AgentHook). Wired into the engine in P6; unused
        # until then and default-empty so callers are unaffected.
        self._hooks: list[Any] = list(hooks or [])
        # Mid-turn follow-up queue. Populated by
        # callers via :meth:`queue_follow_up`; drained by the engine mid-turn so
        # sub-agent/queued results reach the model without ending the turn.
        self._pending_queue: deque[UserMessage] = deque()
        # Compaction summary progress, pushed from the generate_summary delta
        # callback (which runs on the stream's task) and drained by the
        # compaction generator between polls.
        self._compaction_progress: deque[CompactionProgressEvent] = deque()
        # Skills catalog slot. The system prompt carries a snapshot, and a
        # change is delivered as one swapped context message. See
        # :meth:`_ensure_skills_refresh_context`.
        self._skills_state = SkillsCatalogState()

    @property
    def context(self) -> Any:
        return self._context

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def reload_context(self) -> None:
        if self._context_loader is not None:
            self._context = self._context_loader(self._cwd)
        if self._prompt_builder is not None:
            self._system_prompt = self._prompt_builder(self._cwd, self._context, tools=self.tools)

    def _ensure_skills_refresh_context(self) -> None:
        """Announce a skill catalog that changed since the last cold boundary.

        The catalog in the system prompt is a snapshot, and rebuilding the
        prompt to refresh it would invalidate the cached prefix on every
        boundary. So a change is delivered as its own context message, and a
        stale copy is swapped in place rather than appended again.

        Only a real change speaks: a boundary that re-ranks nothing, re-reads
        the same files, or finds the catalog unreadable stays silent. An
        unreadable catalog preserves the last known one rather than announcing
        that no skills exist, which is the same fail-closed rule the project
        trust store uses.
        """
        if self._depth != 0:
            return
        try:
            from vtx.ai.agent.skills_refresh import (
                build_skills_refresh_message,
                render_removed,
                render_update,
            )

            catalog = self._load_skills_catalog_state()
            if catalog is None:
                return
            summaries, fingerprint = catalog

            if self._skills_state.fingerprint == "" and self._skills_state.summaries == ():
                # First boundary of the run: the system prompt already carries
                # this catalog, so announcing it would duplicate the block.
                self._skills_state.summaries = summaries
                self._skills_state.fingerprint = fingerprint
                return
            if fingerprint == self._skills_state.fingerprint:
                return

            previous = self._skills_state.summaries
            text = render_update(previous, summaries) if summaries else render_removed(previous)
            entry_id = self._adopt_skills_refresh_slot()
            message = build_skills_refresh_message(text)
            if entry_id is not None:
                self.session.replace_message(entry_id, message)
            else:
                entry_id = self.session.append_message(message)
            self._skills_state.summaries = summaries
            self._skills_state.fingerprint = fingerprint
        except Exception:
            log.exception("skills catalog refresh delivery failed")

    def _load_skills_catalog_state(self) -> tuple[tuple, str] | None:
        """Summarize the catalog, or None when it cannot be read."""
        from vtx.ai.agent.context.skills import load_skills
        from vtx.ai.agent.skills_refresh import fingerprint_summaries, summarize_skills

        loaded = load_skills(self._cwd)
        summaries = summarize_skills(loaded.skills)
        return summaries, fingerprint_summaries(summaries)

    def _adopt_skills_refresh_slot(self) -> str | None:
        """Reuse a skills-refresh slot already present in a resumed log."""
        from vtx.ai.agent.skills_refresh import is_skills_refresh_message

        for entry in reversed(self.session.active_entries):
            message = getattr(entry, "message", None)
            if message is None or not is_skills_refresh_message(message):
                continue
            return entry.id
        return None

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
            # Images precede text (better grounding); text is optional for
            # image-only submissions.
            parts: list[TextContent | ImageContent] = []
            if query:
                parts.append(TextContent(text=query))
            parts.extend(images)
            user_message = UserMessage(content=parts)
        else:
            user_message = UserMessage(content=query)

        self._ensure_skills_refresh_context()
        self.session.append_message(user_message)

        # Resume from a checkpoint left by a previous cancelled (/stop) run in
        # this session. We surface the partial text/tool results as a
        # continuation prompt so the model picks up where it left off instead
        # of discarding in-flight work. Backward-compatible: only fires when a
        # stale active checkpoint exists.
        restored = self._restore_checkpoint()
        if restored is not None:
            self.session.append_message(restored)

        if self._extensions is not None:
            await self._extensions.emit(AGENT_START, cancel_event=cancel_event)

        yield AgentStartEvent()

        # Deliver anything that settled while the session was idle *before* the
        # first model call of this run. Draining only between turns meant a
        # sub-agent that finished after the parent went idle was appended to
        # the session but not shown to the model until the turn after next —
        # the "it ran, it finished, and I never got the answer" case.
        for evt in self._drain_background_notifications():
            yield evt

        turn = 0
        stop_reason = StopReason.STOP
        was_interrupted = False

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
                async for event in run_agent_turn(
                    AgentRunSpec(
                        provider=self.provider,
                        messages=messages,
                        tools=self.tools,
                        system_prompt=system_prompt,
                        turn=turn,
                        cancel_event=cancel_event,
                        extensions=self._extensions,
                        injection_callback=self._drain_injection_messages,
                        checkpoint_callback=self._persist_checkpoint,
                        hooks=self._hooks,
                    )
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
                        # Turn completed normally — any stale checkpoint from a
                        # previous cancelled run is no longer relevant.
                        self.session.clear_runtime_checkpoint()
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
                    if get_harness_config().compaction_on_overflow == "pause":
                        break
                    # Continue mode: synthetic user message was injected, continue loop
                    continue

                if stop_reason != StopReason.TOOL_USE:
                    break

            if turn >= max_turns and not was_interrupted:
                stop_reason = (
                    StopReason.LENGTH if stop_reason == StopReason.TOOL_USE else stop_reason
                )

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

            await self._extensions.emit(AGENT_SETTLED, cancel_event=cancel_event)

    def _effective_max_turns(self) -> int:
        return get_harness_config().max_turns

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
        out: list[Event] = []

        if self._background_manager is None:
            return out

        try:
            drained = self._background_manager.drain_completed()
        except Exception:
            log.exception("BackgroundTaskManager.drain_completed failed")
            return out

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

    def queue_follow_up(self, message: UserMessage) -> None:
        """Queue a follow-up user message for mid-turn injection.

        Picked up by the engine's injection callback while a turn is running,
        so sub-agent completions / queued prompts are fed into the live turn
        instead of waiting for the next one.
        """
        self._pending_queue.append(message)

    def _drain_injection_messages(self) -> list[UserMessage]:
        """Return and clear queued follow-up messages for the engine.

        Called by the engine between tool execution and the next model call.
        Keeping the queue separate from the between-turns background drain
        avoids double-delivering notifications.
        """
        if not self._pending_queue:
            return []
        drained = list(self._pending_queue)
        self._pending_queue.clear()
        return drained

    def _persist_checkpoint(self, snapshot: dict) -> None:
        """Persist a partial-turn checkpoint to the session for cancel/resume."""
        try:
            self.session.append_runtime_checkpoint(
                partial_content=snapshot.get("partial_content", []),
                tool_results=snapshot.get("tool_results", []),
                text_so_far=snapshot.get("text_so_far", ""),
            )
        except Exception:
            log.exception("failed to persist runtime checkpoint")

    def _restore_checkpoint(self) -> UserMessage | None:
        """Build a continuation prompt from a stale active checkpoint, if any."""
        checkpoint = self.session.load_runtime_checkpoint()
        if checkpoint is None:
            return None
        try:
            text = checkpoint.text_so_far or "(no text captured)"
            n_tools = len(checkpoint.tool_results)
            prompt = (
                "[System: a previous run was interrupted. Resume from the "
                f"partial state below rather than restarting.]\n\n"
                f"Partial assistant text so far:\n{text}\n"
            )
            if n_tools:
                prompt += f"\nTool results already produced this turn: {n_tools}."
            return UserMessage(content=prompt)
        except Exception:
            log.exception("failed to restore runtime checkpoint")
            return None
        finally:
            # The checkpoint is consumed by this resume; deactivate it so it
            # isn't restored again on a later turn.
            self.session.clear_runtime_checkpoint()

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
    ) -> AsyncIterator[CompactionStartEvent | CompactionProgressEvent | CompactionEndEvent]:
        if stop_reason == StopReason.ERROR:
            return

        # Get the latest assistant message that has usage.
        # The most recent assistant entry can be interrupted/error and have no usage.
        # Stop searching if we hit a compaction entry to avoid
        # backtracking to pre-compaction usage.
        last_usage: Usage | None = None
        for entry in reversed(self.session.active_entries):
            if isinstance(entry, CompactionEntry):
                break
            if isinstance(entry, MessageEntry) and isinstance(entry.message, AssistantMessage):
                usage = entry.message.usage
                if usage is None:
                    continue
                last_usage = usage
                break

        if last_usage is None:
            return

        harness_cfg = get_harness_config()
        context_window = self.config.context_window or harness_cfg.default_context_window
        threshold_percent = harness_cfg.compaction_threshold_percent

        if not is_overflow(last_usage, context_window, threshold_percent):
            return

        if cancel_event and cancel_event.is_set():
            return

        if last_usage is not None:
            tokens_before = (
                last_usage.input_tokens
                + last_usage.output_tokens
                + last_usage.cache_read_tokens
                + last_usage.cache_write_tokens
            )
        else:
            tokens_before = int(self.session.token_totals().context_tokens)
        if not tokens_before:
            total_chars = 0
            for msg in self.session.all_messages:
                content = getattr(msg, "content", None)
                if isinstance(content, str):
                    total_chars += len(content)
                elif isinstance(content, list):
                    for part in content:
                        text = getattr(part, "text", None)
                        if isinstance(text, str):
                            total_chars += len(text)
            tokens_before = total_chars // 4

        # Yield start event immediately so UI can show status
        yield CompactionStartEvent(
            tokens_before=tokens_before, context_window=context_window, trigger="overflow"
        )

        if self._extensions is not None:
            await self._extensions.emit(
                COMPACTION_START, cancel_event=cancel_event, tokens_before=tokens_before
            )

        # Progress events are coalesced: a per-token event would flood the UI
        # queue for a summary that takes tens of seconds, and the only signal
        # worth watching is which of the mandated sections has started.
        last_emit = 0.0
        last_sections = 0

        def _on_progress(progress: SummaryProgress) -> None:
            nonlocal last_emit, last_sections
            changed = len(progress.sections_started) != last_sections
            now = time.monotonic()
            if not changed and now - last_emit < COMPACTION_PROGRESS_INTERVAL:
                return
            last_emit = now
            last_sections = len(progress.sections_started)
            self._compaction_progress.append(
                CompactionProgressEvent(
                    chars=progress.chars, sections_started=list(progress.sections_started)
                )
            )

        try:
            # Use all_messages (uncompacted) for summarization so LLM sees full history
            summary_task = asyncio.create_task(
                generate_summary(
                    list(self.session.all_messages),
                    self.provider,
                    system_prompt,
                    on_delta=_on_progress,
                )
            )
            # Poll the summary task so queued progress reaches the UI between
            # tokens; generate_summary has no cancel hook of its own.
            while True:
                done, _pending = await asyncio.wait(
                    {summary_task}, timeout=COMPACTION_PROGRESS_INTERVAL
                )
                while self._compaction_progress:
                    yield self._compaction_progress.popleft()
                if done:
                    break
            summary = summary_task.result()

            # Everything before is summarized, nothing "kept"
            first_kept_id = self.session.leaf_id or ""

            # Estimate tokens of the compacted context
            summary_text = summary

            user_msg = (
                "[Context compacted — conversation history summarized above."
                " Continue working on the task.]"
            )
            tokens_after = (len(summary_text) + len(user_msg)) // 4

            self.session.append_compaction(
                summary=summary,
                first_kept_entry_id=first_kept_id,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
            )

            # In continue mode, inject synthetic continue message that
            # reinforces the active task rather than offering an exit.
            if harness_cfg.compaction_on_overflow == "continue":
                continue_msg = UserMessage(
                    content=(
                        "[context compacted — summary above preserves conversation state]\n"
                        "Pick up exactly where you left off. Do not re-read or"
                        " re-explore files you already have context for from the"
                        " summary above. Continue executing the next step of the"
                        " task described in the summary."
                    )
                )
                self.session.append_message(continue_msg)

            yield CompactionEndEvent(
                tokens_before=tokens_before, tokens_after=tokens_after, summary=summary
            )

            if self._extensions is not None:
                await self._extensions.emit(
                    COMPACTION_END,
                    cancel_event=cancel_event,
                    tokens_before=tokens_before,
                    tokens_after=tokens_after,
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
