"""Idle-time session recap: schedules, generates and displays draft recaps.

After an agent run finishes the idle timer is armed (default 30s); any typing
resets it. When it fires (and the user hasn't interacted), a cheap one-off LLM
call over the recent conversation drafts a concise "where you left off"
summary that is rendered into the chat log and cleared on the next prompt.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from textual.timer import Timer

from vtx.coding_agent.config import config
from vtx.core.recap import (
    RecapContext,
    build_recap_context,
    generate_recap,
    has_meaningful_activity,
)
from vtx.core.types import TextContent, UserMessage
from vtx.tui.chat import ChatLog

if TYPE_CHECKING:
    from textual.worker import Worker

log = logging.getLogger("vtx.tui.recap")


class RecapMixin:
    """Mixed into :class:`vtx.tui.app.Vtx`; owns all recap scheduling state."""

    _is_running: bool
    _runtime: Any  # ConversationRuntime; typed loosely to avoid an import cycle

    if TYPE_CHECKING:
        query_one: Any
        run_worker: Any
        set_timer: Any

    def _init_recap_state(self) -> None:
        self._recap_timer: Timer | None = None
        self._recap_worker: Worker[None] | None = None
        self._recap_key: str | None = None

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def _arm_recap_timer(self) -> None:
        self._cancel_recap_timer()
        if not config.recap.enabled:
            return
        seconds = max(5.0, float(config.recap.idle_seconds))
        self._recap_timer = self.set_timer(seconds, self._on_recap_idle)

    def _reset_recap_timer(self) -> None:
        # Only re-arm while idle; during a run the timer is armed again when
        # the run ends.
        if self._recap_timer is not None and not self._is_running:
            self._arm_recap_timer()

    def _cancel_recap_timer(self) -> None:
        if self._recap_timer is not None:
            self._recap_timer.stop()
            self._recap_timer = None

    def _dismiss_recap(self) -> None:
        """Full teardown before new activity: timer, worker and display."""
        self._cancel_recap_timer()
        if self._recap_worker is not None:
            self._recap_worker.cancel()
            self._recap_worker = None
        chat = self.query_one("#chat-log", ChatLog)
        chat.clear_trailing_status()
        chat.remove_recap()

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def _build_session_recap_context(self) -> tuple[RecapContext, str] | None:
        session = self._runtime.session
        if session is None:
            return None

        messages = session.messages
        if not messages:
            return None

        initial_task = None
        for message in session.all_messages:
            if not isinstance(message, UserMessage):
                continue
            content = (
                message.content
                if isinstance(message.content, str)
                else "\n".join(
                    part.text for part in message.content if isinstance(part, TextContent)
                )
            )
            initial_task = content.strip() or None
            break

        compaction_summary = None
        for entry in reversed(session.active_entries):
            summary_text = getattr(entry, "summary", None)
            if isinstance(summary_text, str) and summary_text.strip():
                compaction_summary = summary_text.strip()
                break

        context = build_recap_context(
            messages, initial_task=initial_task, compaction_summary=compaction_summary
        )
        if not context.messages and not context.broader_context:
            return None

        key = f"{len(messages)}:{type(messages[-1]).__name__}:{str(messages[-1])[-200:]}"
        return context, key

    async def _generate_and_show_recap(self, reason: str) -> None:
        built = self._build_session_recap_context()
        if built is None:
            log.debug("recap(%s): no session messages to recap", reason)
            return
        context, key = built

        provider = self._runtime.provider
        if provider is None:
            log.debug("recap(%s): no provider available", reason)
            return

        manual = reason == "manual"
        if not manual:
            if not has_meaningful_activity(context.messages):
                log.debug("recap(%s): skipped, no meaningful activity", reason)
                return
            if self._recap_key == key:
                log.debug("recap(%s): skipped, already drafted for this state", reason)
                return

        chat = self.query_one("#chat-log", ChatLog)
        chat.show_spinner_status("Drafting recap...")

        try:
            recap = await generate_recap(context, provider)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("recap(%s): generation failed: %s", reason, exc)
            chat.clear_trailing_status()
            if manual:
                chat.add_info_message(f"Recap failed: {exc}", error=True)
            return

        chat.clear_trailing_status()
        if recap is None:
            log.debug("recap(%s): model returned empty text", reason)
            if manual:
                chat.add_info_message("No recap available yet — send a message first.")
            return

        self._recap_key = key
        chat.show_recap(recap)

    def _on_recap_idle(self) -> None:
        self._recap_timer = None
        if not config.recap.enabled or self._is_running:
            return
        self._recap_worker = self.run_worker(
            self._generate_and_show_recap("idle"), group="recap", exclusive=True
        )

    def _handle_recap_command(self) -> None:
        if self._is_running:
            chat = self.query_one("#chat-log", ChatLog)
            chat.add_info_message("Cannot draft a recap while the agent is running.")
            return
        self._cancel_recap_timer()
        self._recap_worker = self.run_worker(
            self._generate_and_show_recap("manual"), group="recap", exclusive=True
        )
