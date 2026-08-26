"""Goal runtime state helpers and selection callbacks.

Command handling was removed in favour of the builtin ``goal`` skill.
The remaining methods provide focus persistence, context injection,
auto-continue, usage charging, and dashboard toggling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vtx.coding_agent.goal.record import objective_title
from vtx.coding_agent.goal.service import GoalError, get_service
from vtx.coding_agent.goal.storage import find_goal_file
from vtx.core.types import ImageContent
from vtx.tui.chat import ChatLog
from vtx.tui.commands.base import CommandSupport

if TYPE_CHECKING:
    pass


class GoalCommands(CommandSupport):
    # App-surface attributes provided by the other mixins / Vtx main class.
    _runtime: Any
    _is_running: bool
    _pending_queue: Any

    if TYPE_CHECKING:

        def _run_agent(self, prompt: str, images: list[ImageContent] | None = None) -> Any: ...
        def _update_queue_display(self) -> None: ...

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _goal_service(self):
        return get_service(self._cwd)

    def _goal_chat(self) -> ChatLog:
        return self.query_one("#chat-log", ChatLog)

    def _refresh_goal_widget(self) -> None:
        widget = None
        try:
            widget = self.query_one("#goal-widget")
        except Exception:
            return
        refresh = getattr(widget, "refresh_goal", None)
        if callable(refresh):
            refresh(self._cwd)

    def _announce_goal_event(self, message: str, *, error: bool = False) -> None:
        self._goal_chat().add_info_message(message, error=error)

    def _show_goal_dashboard(self, renderable) -> None:
        self._goal_chat().add_rich_message(renderable)

    # ------------------------------------------------------------------
    # selection callbacks (used by CompletionUIMixin)
    # ------------------------------------------------------------------

    def _select_goal_focus(self, goal_id: str) -> None:
        service = self._goal_service()
        record = service.get(goal_id)
        if record is None or not record.is_open():
            self._announce_goal_event("That goal is no longer open", error=True)
            return
        service.set_focus(goal_id, reason="selected")
        self._announce_goal_event(f"[vtx-goal] focused: {objective_title(record.objective, 60)}")
        self._refresh_goal_widget()

    def _confirm_goal_clear(self, choice: str) -> None:
        service = self._goal_service()
        if choice != "archive":
            self._announce_goal_event("[vtx-goal] archive cancelled")
            return
        record = service.focused()
        if record is None:
            self._announce_goal_event("[vtx-goal] nothing to archive", error=True)
            return
        archived = service.archive(record.id)
        path = find_goal_file(service.cwd, archived.id)
        location = str(path) if path else ".vtx/goals/archived/"
        self._announce_goal_event(f"[vtx-goal] archived as complete: {location}")
        self._refresh_goal_widget()

    def _apply_goal_setting(self, key: str) -> None:
        service = self._goal_service()
        current = service.settings.get(key)
        if current is None:
            return
        service.update_settings(**{key: not bool(current)})
        state = "off" if bool(current) else "on"
        self._announce_goal_event(f"[vtx-goal] {key}: {state}")
        self._refresh_goal_widget()

    # ------------------------------------------------------------------
    # runtime state: focus persistence, context injection, auto-continue
    # ------------------------------------------------------------------

    FOCUS_ENTRY_TYPE = "vtx-goal-focus"

    def _init_goal_state(self) -> None:
        """Bind focus persistence and apply startup focus resolution."""
        service = self._goal_service()
        session = self._runtime.session

        def persist_focus(goal_id: str | None, reason: str) -> None:
            live = self._runtime.session
            if live is None:
                return
            import json as _json

            live.append_custom_message(
                self.FOCUS_ENTRY_TYPE,
                _json.dumps({"focusedGoalId": goal_id, "reason": reason}),
                display=False,
            )

        service.on_focus_change = persist_focus

        if session is not None and session.entries:
            self._restore_goal_state(session)
        else:
            service.focused_id = None
            self._resolve_startup_focus(service)

    def _resolve_startup_focus(self, service) -> None:
        """No session history: auto-focus the sole open goal when enabled."""
        if service.focused_id is not None and service.focused() is not None:
            return
        pool = service.pool()
        if len(pool) == 1 and service.settings.get("autoSelectSingleGoal", True):
            service.set_focus(next(iter(pool)), reason="resumed")

    def _restore_goal_state(self, session) -> None:
        """Apply the latest persisted focus entry from the session branch."""
        import json as _json

        service = self._goal_service()
        latest: dict | None = None
        for entry in session.active_entries:
            entry_type = getattr(entry, "custom_type", "")
            if entry_type != self.FOCUS_ENTRY_TYPE:
                continue
            try:
                data = _json.loads(getattr(entry, "content", "") or "{}")
            except _json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                latest = data
        if latest is None:
            self._resolve_startup_focus(service)
            self._refresh_goal_widget()
            return
        goal_id = latest.get("focusedGoalId") or None
        if goal_id and service.get(goal_id) is not None:
            service.focused_id = goal_id
        else:
            service.focused_id = None
        self._refresh_goal_widget()

    def _focused_active_goal(self):
        """Focused goal only when the system is enabled and it is running."""
        service = self._goal_service()
        if service.settings.get("disabled"):
            return None, service
        record = service.focused()
        return record, service

    def _goal_context_block(self) -> str:
        """State block prepended to every user prompt while a goal runs."""
        record, service = self._focused_active_goal()
        if record is None or record.status != "active":
            return ""
        from vtx.coding_agent.goal.prompts import goal_context_block

        return goal_context_block(service, record)

    def _goal_auto_continue_prompt(self) -> tuple[str, str] | None:
        """Checkpoint prompt when the agent stops short of the objective."""
        record, service = self._focused_active_goal()
        if record is None:
            return None
        if not service.settings.get("autoContinue", True):
            return None
        if record.status != "active":
            return None
        from vtx.coding_agent.goal.prompts import continuation_prompt

        title = objective_title(record.objective, 60)
        display = f"◈ goal checkpoint · {title}"
        return display, continuation_prompt(record)

    def _pause_goal_on_interrupt(self) -> None:
        """Esc during active work pauses the focused goal (pi behaviour)."""
        record, service = self._focused_active_goal()
        if record is None or record.status != "active":
            return
        try:
            service.set_status(record.id, "paused", reason="interrupted by user")
            self._announce_goal_event(
                f"[vtx-goal] paused: {objective_title(record.objective, 60)} — resume to continue"
            )
        except GoalError:
            pass
        finally:
            self._refresh_goal_widget()

    def _charge_goal_usage(
        self, *, input_tokens: int, output_tokens: int, elapsed_ms: float
    ) -> None:
        record, service = self._focused_active_goal()
        if record is None:
            return
        updated = service.charge_usage(
            record.id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_ms=elapsed_ms,
        )
        if (
            updated is not None
            and updated.status == "budget_limited"
            and (record.status != "budget_limited")
        ):
            self._announce_goal_event(
                "[vtx-goal] token budget reached — goal is budget_limited "
                "(wrap up; resume clears the cap only by editing the file)"
            )
        self._refresh_goal_widget()

    def action_toggle_goal_dashboard(self) -> None:
        """Ctrl+Shift+G: post the expanded unified dashboard to the chat log."""
        from vtx.tui.goal_ui import render_expanded

        service = self._goal_service()
        record = service.focused()
        if record is None:
            self._announce_goal_event("[vtx-goal] no focused goal")
            return
        self._show_goal_dashboard(render_expanded(service, record))
