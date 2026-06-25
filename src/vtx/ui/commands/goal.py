"""``/goal`` slash command — set, inspect, pause, resume, or clear a
completion goal that the agent keeps working toward across turns.

Mirrors the structure of the other command mixins in this package.
The actual evaluator loop lives in :mod:`vtx.goal` and the runtime
exposes :meth:`~vtx.runtime.ConversationRuntime.set_goal` / ``clear_goal``
/ ``pause_goal`` / ``resume_goal`` for mutation.
"""

from __future__ import annotations

import time

from vtx import config

from ..chat import ChatLog
from ..widgets import InfoBar
from .base import CommandSupport


class GoalCommands(CommandSupport):
    """``/goal [objective|status|pause|resume|clear]``."""

    _CLEAR_ALIASES = frozenset({"clear", "stop", "off", "reset", "none", "cancel", "remove"})

    def _format_goal_status(self, goal) -> list[tuple[str, str]]:
        elapsed = ""
        if goal.created_at:
            try:
                from datetime import UTC, datetime

                created = datetime.fromisoformat(goal.created_at)
                delta = max(0, int((datetime.now(UTC) - created).total_seconds()))
                if delta < 60:
                    elapsed = f"{delta}s"
                elif delta < 3600:
                    elapsed = f"{delta // 60}m"
                else:
                    elapsed = f"{delta // 3600}h"
            except Exception:
                elapsed = ""

        rows: list[tuple[str, str]] = []
        rows.append(("status", goal.status))
        if elapsed:
            rows.append(("elapsed", elapsed))
        rows.append(("turns evaluated", str(goal.turns_evaluated)))
        rows.append(("tokens used", f"{goal.tokens_used:,}"))
        if goal.max_turns_override:
            rows.append(("turn cap", str(goal.max_turns_override)))
        if goal.last_reason:
            rows.append(("last reason", goal.last_reason))
        if goal.completed_at:
            rows.append(("completed at", goal.completed_at))
        return rows

    def _show_goal_status(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        goal = self._runtime.goal_manager.goal
        if goal is None:
            chat.add_info_message("No active goal. Set one with /goal <completion condition>.")
            return

        rows = self._format_goal_status(goal)
        # Render as a simple info block (mirrors the style used by
        # add_session_details without the multi-column alignment).
        objective = goal.objective
        body = [f"Goal: {objective}", ""]
        max_key = max((len(k) for k, _ in rows), default=8)
        for key, value in rows:
            body.append(f"  {key.ljust(max_key)}  {value}")
        chat.add_info_message("\n".join(body))

    def _set_goal(self, objective: str) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        info_bar = self.query_one("#info-bar", InfoBar)
        if self._is_running:
            chat.add_info_message(
                "Cannot set a goal while the agent is running. "
                "Use /goal <objective> between turns.",
                error=True,
            )
            return
        if not config.goal.enabled:
            chat.add_info_message(
                "Goals are disabled. Set goal.enabled: true in ~/.vtx/config.yml.", error=True
            )
            return
        if self._runtime.session is None:
            chat.add_info_message("Agent not initialized", error=True)
            return
        try:
            goal, warning = self._runtime.set_goal(objective)
        except (RuntimeError, ValueError) as exc:
            chat.add_info_message(str(exc), error=True)
            return
        # Show the truncated objective in the chat so the user has a
        # visible record of what they set, plus any budget-clause
        # warning the parser emitted.
        body = f"Goal set: {goal.objective}"
        if warning:
            body = f"{body}\n  {warning}"
        chat.add_info_message(body)
        info_bar.set_goal("active", started_at=time.time())

    def _clear_goal(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        info_bar = self.query_one("#info-bar", InfoBar)
        prior = self._runtime.clear_goal()
        info_bar.set_goal("")
        if prior is None:
            chat.add_info_message("No active goal to clear.")
            return
        chat.add_info_message(f"Goal cleared (was: {prior.objective})")

    def _pause_goal(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        info_bar = self.query_one("#info-bar", InfoBar)
        paused = self._runtime.pause_goal()
        if paused is None:
            chat.add_info_message("No active goal to pause.", error=True)
            return
        info_bar.set_goal("paused", started_at=time.time())
        chat.add_info_message(f"Goal paused: {paused.objective}")

    def _resume_goal(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        info_bar = self.query_one("#info-bar", InfoBar)
        resumed = self._runtime.resume_goal()
        if resumed is None:
            chat.add_info_message("No paused goal to resume.", error=True)
            return
        info_bar.set_goal("active", started_at=time.time())
        chat.add_info_message(f"Goal resumed: {resumed.objective}")

    def _handle_goal_command(self, args: str) -> None:
        sub = args.strip()
        if not sub:
            self._show_goal_status()
            return
        lowered = sub.lower()
        if lowered in self._CLEAR_ALIASES:
            self._clear_goal()
        elif lowered == "pause":
            self._pause_goal()
        elif lowered == "resume":
            self._resume_goal()
        elif lowered in ("status", "show"):
            self._show_goal_status()
        else:
            self._set_goal(sub)


__all__ = ["GoalCommands"]
