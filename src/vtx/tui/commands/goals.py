"""Goal slash commands: the vtx-goal command palette.

Commands mirror pi-goal-x's fourteen-command surface::

    /goal [seed]            guided regular draft
    /sisyphus [seed]        guided ordered draft
    /goal-direct <obj>      create immediately
    /sisyphus-direct <obj>  create ordered immediately
    /goal-list              list open goals
    /goal-status            unified dashboard (verbose | health)
    /goal-focus             pick session focus
    /goal-unfocus           clear session focus
    /goal-tweak <change>    guided revision of the focused goal
    /goal-pause             pause focused goal
    /goal-resume            resume paused/blocked goal
    /goal-clear             archive after confirmation
    /goal-cancel            discard an unconfirmed draft
    /goal-settings          toggle goal behaviour
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text

from vtx.coding_agent.goal.record import count_tasks, objective_title
from vtx.coding_agent.goal.service import GoalError, get_service, goal_progress
from vtx.coding_agent.goal.storage import find_goal_file, format_file_timestamp
from vtx.tui.chat import ChatLog
from vtx.tui.commands.base import CommandSupport
from vtx.tui.floating_list import ListItem
from vtx.tui.goal_ui import render_expanded

if TYPE_CHECKING:
    pass


class GoalCommands(CommandSupport):
    # App-surface attributes provided by the other mixins / Vtx main class.
    _runtime: Any
    _is_running: bool
    _pending_queue: Any

    if TYPE_CHECKING:

        def _run_agent(self, prompt: str) -> Any: ...
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

    def _submit_goal_prompt(self, display: str, query: str) -> None:
        """Start (or queue) an agent turn carrying ``query``."""
        chat = self._goal_chat()
        if self._is_running:
            if len(self._pending_queue) >= 5:
                chat.add_info_message("Queue full; cannot start goal turn", error=True)
                return
            self._pending_queue.append((display, query))
            if hasattr(self, "_update_queue_display"):
                self._update_queue_display()
            return
        chat.add_user_message(display)
        self._is_running = True
        self.run_worker(self._run_agent(query), exclusive=True)

    # ------------------------------------------------------------------
    # creation
    # ------------------------------------------------------------------

    def _handle_goal_command(self, args: str, *, mode: str = "regular") -> None:
        service = self._goal_service()
        if service.settings.get("disabled"):
            self._announce_goal_event(
                "The goal system is disabled (/goal-settings to re-enable)", error=True
            )
            return
        seed = args.strip()
        prompt_seed = f"\n\nUser-provided seed: {seed}" if seed else ""
        display = f"/{mode}-goal" + (f" {seed}" if seed else "")
        query = (
            "<vtx-goal-cmd>"
            f"The user invoked /{'goal' if mode == 'regular' else 'sisyphus'} to set up a "
            f"{mode} goal for this project.{prompt_seed}\n"
            "Run the guided drafting process described in the vtx-goal-drafting "
            "block of your instructions: clarify only what is necessary, propose the "
            "objective and task plan, and create the goal only after explicit user "
            "confirmation.\n"
            "</vtx-goal-cmd>"
        )
        self._submit_goal_prompt(display, query)

    def _handle_sisyphus_command(self, args: str) -> None:
        self._handle_goal_command(args, mode="sisyphus")

    def _create_direct(self, args: str, *, mode: str) -> None:
        chat = self._goal_chat()
        objective = args.strip()
        if not objective:
            chat.add_info_message(f"Usage: /{mode}-direct <complete objective>", error=True)
            return
        service = self._goal_service()
        try:
            record = service.create(objective, mode=mode, source="command-direct")
        except GoalError as exc:
            chat.add_info_message(str(exc), error=True)
            return
        except ValueError as exc:
            chat.add_info_message(str(exc), error=True)
            return
        done, total = count_tasks(record.tasks)
        summary = objective_title(record.objective, 70)
        chat.add_info_message(
            f"[vtx-goal] created {mode} goal {record.id}: {summary} "
            f"(tasks {done}/{total}) — starting now"
        )
        self._refresh_goal_widget()
        query = (
            "<vtx-goal-cmd>"
            f"A new {mode} goal was just created and focused:\n"
            f"{record.objective.strip()}\n"
            "Begin working toward it immediately"
            + (" in the stated order" if mode == "sisyphus" else "")
            + '; use goal(action="set_tasks") to lay out the plan first.'
            "</vtx-goal-cmd>"
        )
        self._submit_goal_prompt(f"/{mode}-direct {summary}", query)

    # ------------------------------------------------------------------
    # listing / status
    # ------------------------------------------------------------------

    def _handle_goal_list(self) -> None:
        chat = self._goal_chat()
        service = self._goal_service()
        pool = service.pool()
        if not pool:
            chat.add_info_message("[vtx-goal] no open goals — start one with /goal")
            return
        lines = Text()
        lines.append("✓ Open goals:\n", style="bold")
        for record in pool.values():
            done, total, pct = goal_progress(record)
            marker = "▸" if record.id == service.focused_id else " "
            path = find_goal_file(service.cwd, record.id)
            stamp = format_file_timestamp(path.name) if path else ""
            lines.append(f"{marker} [{record.id}] {objective_title(record.objective, 58)}\n")
            meta = f"    {record.mode} · {record.label()} · tasks {done}/{total} ({pct}%)"
            if record.usage.elapsed_ms or record.usage.total_tokens():
                from vtx.tui.goal_ui import format_usage

                meta += f" · {format_usage(record)}"
            if stamp:
                meta += f" · created {stamp}"
            lines.append(meta + "\n", style="dim")
        self._show_goal_dashboard(lines)

    def _handle_goal_status(self, args: str) -> None:
        service = self._goal_service()
        sub = args.strip().lower()

        if sub == "health":
            self._announce_goal_event(self._goal_health_report(service))
            return

        record = service.focused()
        if record is None:
            pool = service.pool()
            if pool:
                self._announce_goal_event(
                    f"[vtx-goal] no session focus — choose one with /goal-focus ({len(pool)} open)"
                )
            else:
                self._announce_goal_event("[vtx-goal] no open goals")
            return

        dashboard = render_expanded(service, record)
        if sub == "verbose":
            verbose = Text(dashboard.plain + "\n\n")
            verbose.append("Settings: ", style="bold")
            verbose.append(str(service.settings))
            self._show_goal_dashboard(verbose)
            return
        self._show_goal_dashboard(dashboard)

    def _goal_health_report(self, service) -> str:
        pool = service.pool()
        problems: list[str] = []
        if service.focused_id and service.focused_id not in pool:
            problems.append("session focus points at a missing or closed goal")
        ledger_issues = 0
        from vtx.coding_agent.goal.storage import read_ledger

        for event in read_ledger(service.cwd, limit_last=500):
            if not isinstance(event, dict) or not event.get("type"):
                ledger_issues += 1
        lines = [
            "[vtx-goal] health",
            f"open goals: {len(pool)}",
            f"focused id: {service.focused_id or '(none)'}",
            f"malformed ledger entries (last 500): {ledger_issues}",
        ]
        for record in pool.values():
            done, total = count_tasks(record.tasks)
            if total and done == total and record.status == "active":
                lines.append(
                    f"note: [{record.id}] all tasks complete but still active — "
                    'finish via goal(action="update", status="complete")'
                )
        lines.extend(f"issue: {p}" for p in problems)
        report = "\n".join(lines)
        return "[vtx-goal] health OK:\n" + report if not problems else report

    # ------------------------------------------------------------------
    # focus management
    # ------------------------------------------------------------------

    def _handle_goal_focus(self) -> None:
        from vtx.tui.selection_mode import SelectionMode

        chat = self._goal_chat()
        service = self._goal_service()
        pool = service.pool()
        if not pool:
            chat.add_info_message("[vtx-goal] no open goals to focus")
            return
        if len(pool) == 1:
            self._select_goal_focus(next(iter(pool)))
            return

        current = service.focused_id
        items: list[ListItem[str]] = []
        descriptions: dict[str, str] = {}
        for record in pool.values():
            done, total, pct = goal_progress(record)
            label = f"{record.id}  {objective_title(record.objective, 48)}"
            if record.id == current:
                label += " ✓"
            value = record.id
            descriptions[value] = (
                f"{record.mode} · {record.label()} · tasks {done}/{total} ({pct}%)"
            )
            items.append(ListItem(value=value, label=label, description=descriptions[value]))
        self._show_selection_picker(items, SelectionMode.GOAL_FOCUS)

    def _select_goal_focus(self, goal_id: str) -> None:
        service = self._goal_service()
        record = service.get(goal_id)
        if record is None or not record.is_open():
            self._announce_goal_event("That goal is no longer open", error=True)
            return
        service.set_focus(goal_id, reason="selected")
        self._announce_goal_event(f"[vtx-goal] focused: {objective_title(record.objective, 60)}")
        self._refresh_goal_widget()

    def _handle_goal_unfocus(self) -> None:
        service = self._goal_service()
        if service.focused_id is None:
            self._announce_goal_event("[vtx-goal] this session has no goal focus")
            return
        title = ""
        record = service.focused()
        if record is not None:
            title = objective_title(record.objective, 50)
        service.unfocus()
        self._announce_goal_event(f"[vtx-goal] unfocused ({title}) — goal stays open")
        self._refresh_goal_widget()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def _handle_goal_tweak(self, args: str) -> None:
        change = args.strip()
        if not change:
            self._announce_goal_event("Usage: /goal-tweak <change>", error=True)
            return
        service = self._goal_service()
        record = service.focused()
        if record is None:
            self._announce_goal_event("[vtx-goal] no focused goal to tweak", error=True)
            return
        query = (
            "<vtx-goal-cmd>"
            "The user wants to revise the focused goal:\n"
            f"Change: {change}\n\n"
            "Run the guided revision flow: propose the updated objective and/or task "
            "Run the guided revision flow: propose the updated objective and/or task "
            "plan, get explicit confirmation, then apply it with "
            'goal(action="update", status="revise") and/or '
            'goal(action="set_tasks").\n'
            "</vtx-goal-cmd>"
        )
        self._submit_goal_prompt(f"/goal-tweak {objective_title(change, 60)}", query)

    def _handle_goal_pause(self) -> None:
        service = self._goal_service()
        record = service.focused()
        if record is None:
            self._announce_goal_event("[vtx-goal] no focused goal", error=True)
            return
        updated = service.set_status(record.id, "paused", reason="paused by user")
        self._announce_goal_event(
            f"[vtx-goal] paused: {objective_title(updated.objective, 60)} — "
            "/goal-resume to continue"
        )
        self._refresh_goal_widget()

    def _handle_goal_resume(self) -> None:
        service = self._goal_service()
        record = service.focused()
        if record is None:
            self._announce_goal_event("[vtx-goal] no focused goal", error=True)
            return
        if record.status == "active":
            self._announce_goal_event("[vtx-goal] goal is already active")
            return
        updated = service.set_status(record.id, "active")
        self._announce_goal_event(f"[vtx-goal] resumed: {objective_title(updated.objective, 60)}")
        self._refresh_goal_widget()
        query = (
            "<vtx-goal-cmd>"
            "The focused goal was resumed by the user. Continue working toward it "
            "now, picking up from the recorded progress.\n</vtx-goal-cmd>"
        )
        self._submit_goal_prompt("/goal-resume", query)

    def _handle_goal_clear(self) -> None:
        from vtx.tui.selection_mode import SelectionMode

        chat = self._goal_chat()
        service = self._goal_service()
        record = service.focused()
        if record is None:
            chat.add_info_message("[vtx-goal] no focused goal to archive", error=True)
            return
        summary = objective_title(record.objective, 56)
        items = [
            ListItem(
                value="archive",
                label=f"Archive  {summary}",
                description="Move the goal to .vtx/goals/archived/",
            ),
            ListItem(value="cancel", label="Cancel", description="Keep the goal open"),
        ]
        self._show_selection_picker(items, SelectionMode.GOAL_CLEAR)

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

    def _handle_goal_cancel(self) -> None:
        # Drafts are ordinary turns in this implementation: cancelling simply
        # tells the agent to drop any pending proposal.
        self._announce_goal_event(
            "[vtx-goal] draft cancelled — any unconfirmed proposal is discarded"
        )

    # ------------------------------------------------------------------
    # settings
    # ------------------------------------------------------------------

    def _handle_goal_settings(self) -> None:
        from vtx.tui.selection_mode import SelectionMode

        service = self._goal_service()
        settings = service.settings
        rows: dict[str, tuple[bool, str]] = {
            "disabled": (settings.get("disabled", False), "disable the whole goal system"),
            "autoContinue": (
                settings.get("autoContinue", True),
                "auto-continue checkpoint turns while a goal is active",
            ),
            "disableTasks": (
                settings.get("disableTasks", False),
                "hide the goal tool's set_tasks/update_task actions",
            ),
            "auditorEnabled": (
                settings.get("auditorEnabled", True),
                "independent completion audit before archiving",
            ),
            "autoSelectSingleGoal": (
                settings.get("autoSelectSingleGoal", True),
                "auto-focus when exactly one open goal exists",
            ),
        }
        items = [
            ListItem(value=key, label=key, description=text) for key, (_, text) in rows.items()
        ]
        self._show_selection_picker(items, SelectionMode.GOAL_SETTINGS)

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
                f"[vtx-goal] paused: {objective_title(record.objective, 60)} — "
                "/goal-resume to continue"
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
                "(wrap up; /goal-resume clears the cap only by editing the file)"
            )
        self._refresh_goal_widget()

    def action_toggle_goal_dashboard(self) -> None:
        """Ctrl+Shift+G: post the expanded unified dashboard to the chat log."""
        service = self._goal_service()
        record = service.focused()
        if record is None:
            self._announce_goal_event("[vtx-goal] no focused goal")
            return
        from vtx.tui.goal_ui import render_expanded

        self._show_goal_dashboard(render_expanded(service, record))
