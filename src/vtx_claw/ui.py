"""Programmatically extend Vtx TUI for vtx-claw features and slash commands."""

from __future__ import annotations

import argparse
import time
from typing import Any, cast

from vtx.ui.app import Vtx
from vtx.ui.autocomplete import DEFAULT_COMMANDS, SlashCommand
from vtx.ui.chat import ChatLog
from vtx.ui.floating_list import ListItem
from vtx.ui.input import InputBox
from vtx.ui.launch import _print_exit_message

CLAW_ACTIONS = ["status", "start", "stop", "onboard", "help"]

CLAW_DESCRIPTIONS = {
    "status": "Check gateway daemon status and configured channels",
    "start": "Start the background gateway daemon",
    "stop": "Stop the background gateway daemon",
    "onboard": "Perform interactive first-time setup for vtx-claw",
    "help": "Show help on the claw command suite",
}


class ClawVtx(Vtx):
    def _handle_command(self, text: str) -> bool:
        parts = text[1:].split(maxsplit=1)
        cmd = parts[0] if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "claw":
            self._handle_claw_command(args)
            return True

        return super()._handle_command(text)

    def _apply_selection_mode_choice(
        self, item: ListItem, input_box: InputBox, was_at_bottom: bool
    ) -> None:
        if self._selection_mode == "claw":
            self._execute_claw_action(item.value)
            self._restore_chat_scroll_after_refresh(was_at_bottom)
            return
        super()._apply_selection_mode_choice(item, input_box, was_at_bottom)

    def _handle_claw_command(self, args: str) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        requested = args.strip().lower()
        if requested:
            if requested in CLAW_ACTIONS:
                self._execute_claw_action(requested)
            else:
                valid = ", ".join(CLAW_ACTIONS)
                chat.add_info_message(
                    f"Invalid claw action: {requested}. Use one of: {valid}", error=True
                )
            return

        items = [
            ListItem(value=action, label=action, description=CLAW_DESCRIPTIONS[action])
            for action in CLAW_ACTIONS
        ]
        self._selection_mode = cast(Any, "claw")
        self._show_selection_picker(items, cast(Any, "claw"))

    def _execute_claw_action(self, action: str) -> None:
        chat = self.query_one("#chat-log", ChatLog)

        if action == "status":
            try:
                from vtx_claw.config.schema import CHANNEL_FIELD_NAMES, load_claw_config
                from vtx_claw.daemon import PIDManager
            except ImportError:
                chat.add_info_message("vtx-claw is not installed or available.", error=True)
                return

            pid = PIDManager().read()
            status_str = f"Running (PID: {pid})" if pid else "Stopped"

            lines = []
            lines.append(f"Gateway Status: [bold]{status_str}[/bold]")

            try:
                claw_cfg = load_claw_config()
                lines.append(f"Host/Port: {claw_cfg.gateway.host}:{claw_cfg.gateway.port}")
                lines.append(f"Auth Policy: {claw_cfg.auth.default_policy}")
                sb_status = (
                    "[green]Enabled[/green]" if claw_cfg.sandbox.enabled else "[red]Disabled[/red]"
                )
                lines.append(f"Sandbox: {sb_status}")

                cron_status = (
                    "[green]Enabled[/green]" if claw_cfg.cron.enabled else "[red]Disabled[/red]"
                )
                lines.append(f"Cron Scheduler: {cron_status}")

                channels = []
                for field_name in CHANNEL_FIELD_NAMES:
                    if getattr(claw_cfg.channels, field_name).enabled:
                        channels.append(field_name)
                lines.append(f"Enabled Channels: {', '.join(channels) if channels else 'None'}")

                if claw_cfg.persona:
                    lines.append(f"Active Persona: {claw_cfg.persona.active}")
            except Exception as e:
                lines.append(f"Config load error: {e}")

            chat.add_info_message("\n".join(lines))

        elif action == "start":
            import subprocess

            try:
                subprocess.Popen(["vtx-claw", "start", "--daemon"])
                chat.add_info_message("Starting vtx-claw gateway in background...", warning=True)
            except Exception as e:
                chat.add_info_message(f"Failed to start vtx-claw gateway: {e}", error=True)

        elif action == "stop":
            import subprocess

            try:
                subprocess.Popen(["vtx-claw", "stop"])
                chat.add_info_message("Stopping vtx-claw gateway...", warning=True)
            except Exception as e:
                chat.add_info_message(f"Failed to stop vtx-claw gateway: {e}", error=True)

        elif action == "onboard":
            chat.add_info_message(
                "To run interactive onboarding, exit the TUI and run:\n"
                "[bold]vtx-claw onboard[/bold]"
            )

        elif action == "help":
            help_text = (
                "[bold]Claw Commands Help[/bold]\n"
                "/claw status  - Show gateway status and configured channels\n"
                "/claw start   - Start the background gateway daemon\n"
                "/claw stop    - Stop the background gateway daemon\n"
                "/claw onboard - Show onboarding instructions\n"
                "/claw help    - Show this help message"
            )
            chat.add_info_message(help_text)


def run_tui(args: argparse.Namespace) -> None:
    # Programmatically register autocomplete suggestions
    if not any(c.name == "claw" for c in DEFAULT_COMMANDS):
        DEFAULT_COMMANDS.append(
            SlashCommand("claw", "vtx-claw gateway daemon management & status")
        )

    app = ClawVtx(
        model=args.model,
        provider=args.provider,
        api_key=args.api_key,
        base_url=args.base_url,
        resume_session=args.resume_session,
        continue_recent=args.continue_recent,
        openai_compat_auth_mode=args.openai_compat_auth,
        anthropic_compat_auth_mode=args.anthropic_compat_auth,
        extra_extension_paths=list(getattr(args, "extension_paths", None) or []),
        auto_discover_extensions=not getattr(args, "no_extensions", False),
        active_agent=getattr(args, "agent", None),
        extra_agent_paths=list(getattr(args, "agent_files", None) or []),
        auto_discover_agents=not getattr(args, "no_agents", False),
        initial_goal=getattr(args, "goal", None),
    )
    app.run()

    # Fire session_end on the extension bus once the TUI is torn down.
    if app._loaded_extensions.bus.handler_count("session_end"):
        app._loaded_extensions.bus.emit_sync(
            "session_end", cwd=app._cwd, session_id=app._session.id if app._session else ""
        )

    hints = list(app._exit_hints)
    session_id: str | None = None
    duration: float | None = None
    file_changes: dict[str, tuple[int, int]] | None = None

    if app._session:
        session_id = app._session.id
        file_changes = app._session.file_changes_summary() or None
    if app._session_start_time is not None:
        duration = time.time() - app._session_start_time

    if hints or session_id:
        _print_exit_message(hints, session_id, duration, file_changes)
