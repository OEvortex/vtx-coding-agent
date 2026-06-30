"""Programmatically extend Vtx TUI for vtx-claw features and slash commands.

vtx_claw uses vtx as its UI core, patching and extending it entirely from
within this file — no changes to ``vtx/`` itself.

Strategy
--------
1. **Subclass** ``Vtx`` for the main app (as the docs recommend).
2. **Subclass widgets** (``InfoBar``, ``ChatLog``, etc.) where claw needs
   different rendering or extra state.
3. **Override compose** to inject claw-specific widgets.
4. **Monkey-patch** ``vtx.ui.launch._print_exit_message`` at import time so
   the exit summary shows the "vtx-claw" logo instead of the default "VTX"
   logo — no splash-file / CSS override needed.
"""

from __future__ import annotations

import argparse
import time
from typing import Any

import vtx.ui.launch as _launch_mod
from rich.text import Text
from textual.app import ComposeResult
from vtx import config as vtx_config
from vtx.ui.app import Vtx
from vtx.ui.autocomplete import DEFAULT_COMMANDS, SlashCommand
from vtx.ui.chat import ChatLog
from vtx.ui.floating_list import FloatingList, ListItem
from vtx.ui.input import InputBox
from vtx.ui.launch import _print_exit_message
from vtx.ui.queue_ui import QueueDisplay
from vtx.ui.selection_mode import SelectionMode
from vtx.ui.tree import TreeSelector
from vtx.ui.widgets import InfoBar, StatusLine

# ---------------------------------------------------------------------------
# Monkey-patch vtx's exit-message logo so the "vtx-claw" TUI shows a distinct
# brand identity.  The original _print_exit_message uses a hard-coded VTX
# ASCII-art logo; we replace the module-level _LOGO variable with a claw one.
# ---------------------------------------------------------------------------

_CLAW_LOGO = [
    " ██████╗██╗      █████╗ ██╗    ██╗",
    "██╔════╝██║     ██╔══██╗██║    ██║",
    "██║     ██║     ███████║██║ █╗ ██║",
    "██║     ██║     ██╔══██║██║███╗██║",
    "╚██████╗███████╗██║  ██║╚███╔███╔╝",
    " ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝ ",
]

_launch_mod._LOGO = _CLAW_LOGO

# ---------------------------------------------------------------------------
# Monkey-patch the in-TUI splash logo so it shows "CLAW" + "vtx-claw" instead
# of the default VTX logo + version number.
# ---------------------------------------------------------------------------

_CLAW_SPLASH_LINES = tuple(_CLAW_LOGO)

_orig_add_session_info = ChatLog.add_session_info


def _claw_add_session_info(self: ChatLog, version: str) -> None:
    """Replace VTX logo with CLAW logo and show 'vtx-claw' label."""
    from vtx_claw.version import VERSION as CLAW_VERSION

    info_text = Text()
    accent = vtx_config.ui.colors.accent
    dim = vtx_config.ui.colors.dim
    muted = vtx_config.ui.colors.muted

    for i, line in enumerate(_CLAW_SPLASH_LINES):
        info_text.append(line, style=accent)
        if i == len(_CLAW_SPLASH_LINES) - 1:
            info_text.append(f" v{CLAW_VERSION}", style=dim)
        info_text.append("\n")

    if vtx_config.ui.show_welcome_shortcuts:
        info_text.append("\n")

        shortcut_rows = (
            (
                ("/", "slash commands"),
                ("@", "files/dirs"),
                ("tab", "complete paths"),
                ("↑/↓", "history"),
            ),
            (
                ("shift+tab", "permissions"),
                ("esc", "to interrupt"),
                ("shift+enter", "add newline"),
            ),
            (
                ("ctrl+c", "clear input"),
                ("ctrl+c x2", "exit"),
                ("enter", "queue"),
                ("alt+enter", "steer"),
            ),
            (
                ("↑/↓", "select queue"),
                ("ctrl+t", "cycle thinking"),
                ("ctrl+shift+t", "toggle thinking"),
            ),
        )

        for row_idx, row in enumerate(shortcut_rows):
            for item_idx, (key, desc) in enumerate(row):
                if item_idx > 0:
                    info_text.append(" • ", style=dim)
                info_text.append(key, style=muted)
                info_text.append(f" {desc}", style=dim)
            if row_idx < len(shortcut_rows) - 1:
                info_text.append("\n")

    info_text.rstrip()

    from textual.widgets import Label

    info_label = Label(info_text)
    info_label.add_class("session-info")
    self.mount(info_label, before=0)


ChatLog.add_session_info = _claw_add_session_info  # ty:ignore[invalid-assignment]

# ---------------------------------------------------------------------------
# Claw-command catalog — used by the slash command and the selection picker.
# ---------------------------------------------------------------------------

CLAW_ACTIONS = [
    "status",
    "start",
    "stop",
    "channels",
    "skills",
    "memory",
    "persona",
    "voice",
    "sandbox",
    "cron",
    "onboard",
    "help",
]

CLAW_DESCRIPTIONS: dict[str, str] = {
    "status": "Check gateway daemon status and configured channels",
    "start": "Start the background gateway daemon",
    "stop": "Stop the background gateway daemon",
    "channels": "List enabled messaging channels",
    "skills": "List loaded claw skills",
    "memory": "Show memory store summary",
    "persona": "Show active persona",
    "voice": "Show voice configuration status",
    "sandbox": "Show sandbox configuration status",
    "cron": "Show scheduled cron jobs",
    "onboard": "Perform interactive first-time setup for vtx-claw",
    "help": "Show help on the claw command suite",
}


# ---------------------------------------------------------------------------
# ClawInfoBar — extends InfoBar to show daemon status on the left of row 2.
# The daemon status is checked lazily and displayed as a small badge.
# ---------------------------------------------------------------------------


class ClawInfoBar(InfoBar):
    """InfoBar variant that prepends a claw daemon status badge."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._claw_status: str | None = None  # None = unknown, "running", "stopped"

    def set_claw_status(self, status: str | None) -> None:
        """Update the cached daemon status and re-render."""
        if self._claw_status == status:
            return
        self._claw_status = status
        self._label_row2_left.update(self._format_row2_left(), layout=False)

    def _format_row2_left(self) -> Text:
        """Override to prepend claw status badge before the permission mode."""
        result = Text()
        if self._claw_status == "running":
            result.append("🦞 ", style="bold green")
        elif self._claw_status == "stopped":
            result.append("🦞 ", style="red")
        # Delegate to the parent formatting (permission mode + file changes).
        parent_text = super()._format_row2_left()
        result.append_text(parent_text)
        return result

    def _format_permission_mode(self) -> Text:
        """Override to keep the permission-mode rendering but without claw
        status — already handled in _format_row2_left above."""
        # Replicate the parent's _format_permission_mode so we don't get a
        # double-badge.  We already added the claw badge in _format_row2_left,
        # so just return the permission-marker without any prefix.
        result = Text()
        if self._permission_mode == "auto":
            result.append("✓ auto", style=vtx_config.ui.colors.badge.label)
        else:
            result.append("⏹ prompt", style=vtx_config.ui.colors.notice)
        return result


# ---------------------------------------------------------------------------
# ClawVtx — the main TUI application for vtx-claw.
# ---------------------------------------------------------------------------


def _check_daemon_status() -> str | None:
    """Return ``"running"``, ``"stopped"``, or ``None`` on error."""
    try:
        from vtx_claw.daemon import PIDManager

        pid = PIDManager().read()
        return "running" if pid else "stopped"
    except Exception:
        return None


class ClawVtx(Vtx):
    TITLE = "vtx-claw"

    # -- Action dispatch table ------------------------------------------------

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._action_handlers: dict[str, Any] = {
            "status": self._action_status,
            "start": self._action_start,
            "stop": self._action_stop,
            "channels": self._action_channels,
            "skills": self._action_skills,
            "memory": self._action_memory,
            "persona": self._action_persona,
            "voice": self._action_voice,
            "sandbox": self._action_sandbox,
            "cron": self._action_cron,
            "onboard": self._action_onboard,
            "help": self._action_help,
        }

    # -- Compose --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Override compose to inject ClawInfoBar instead of InfoBar.

        This mirrors Vtx.compose exactly except we yield ClawInfoBar instead
        of InfoBar — no other vtx widgets are modified.
        """
        yield ChatLog(id="chat-log")
        yield QueueDisplay(id="queue-display")
        yield StatusLine(id="status-line")
        yield InputBox(cwd=self._cwd, id="input-box")
        yield FloatingList(window_size=10, label_width=6, id="completion-list")
        yield TreeSelector(id="tree-selector")
        info_bar = ClawInfoBar(
            cwd=self._cwd,
            model=self._runtime.model,
            thinking_level=self._runtime.thinking_level,
            hide_thinking=self._hide_thinking,
            id="info-bar",
        )
        if self._runtime.active_agent:
            info_bar._active_agent = self._runtime.active_agent.definition.name
        yield info_bar

    # -- Slash command routing ------------------------------------------------

    def _handle_command(self, text: str) -> bool:
        parts = text[1:].split(maxsplit=1)
        cmd = parts[0] if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "claw":
            self._handle_claw_command(args)
            return True

        return super()._handle_command(text)

    # -- Selection-mode overrides ---------------------------------------------

    def _apply_selection_mode_choice(
        self, item: ListItem, input_box: InputBox, was_at_bottom: bool
    ) -> None:
        if self._selection_mode == "claw":
            self._execute_claw_action(item.value)
            self._restore_chat_scroll_after_refresh(was_at_bottom)
            return
        super()._apply_selection_mode_choice(item, input_box, was_at_bottom)

    # -- Claw command handling ------------------------------------------------

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
        self._selection_mode = SelectionMode.CLAW
        self._show_selection_picker(items, SelectionMode.CLAW)

    def _execute_claw_action(self, action: str) -> None:
        handler = self._action_handlers.get(action)
        if handler is None:
            chat = self.query_one("#chat-log", ChatLog)
            chat.add_info_message(f"Unknown claw action: {action}", error=True)
            return
        handler()

    # -- Action implementations ----------------------------------------------

    def _chat(self) -> ChatLog:
        return self.query_one("#chat-log", ChatLog)

    def _action_status(self) -> None:
        from vtx_claw.config.schema import CHANNEL_FIELD_NAMES, load_claw_config
        from vtx_claw.daemon import PIDManager

        chat = self._chat()
        pid = PIDManager().read()
        status_str = f"Running (PID: {pid})" if pid else "Stopped"

        lines = [f"Gateway Status: [bold]{status_str}[/bold]"]

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

            channels = [
                name for name in CHANNEL_FIELD_NAMES if getattr(claw_cfg.channels, name).enabled
            ]
            lines.append(f"Enabled Channels: {', '.join(channels) if channels else 'None'}")

            if claw_cfg.persona:
                lines.append(f"Active Persona: {claw_cfg.persona.active}")
        except Exception as e:
            lines.append(f"Config load error: {e}")

        chat.add_info_message("\n".join(lines))
        # Also update the info bar badge
        self._update_claw_status()

    def _action_start(self) -> None:
        import subprocess

        try:
            subprocess.Popen(["vtx-claw", "start", "--daemon"])
            self._chat().add_info_message(
                "Starting vtx-claw gateway in background...", warning=True
            )
        except Exception as e:
            self._chat().add_info_message(f"Failed to start vtx-claw gateway: {e}", error=True)
        self._update_claw_status()

    def _action_stop(self) -> None:
        import subprocess

        try:
            subprocess.Popen(["vtx-claw", "stop"])
            self._chat().add_info_message("Stopping vtx-claw gateway...", warning=True)
        except Exception as e:
            self._chat().add_info_message(f"Failed to stop vtx-claw gateway: {e}", error=True)
        self._update_claw_status()

    def _action_help(self) -> None:
        lines = ["[bold]Claw Commands Help[/bold]"]
        widths = max(len(a) for a in CLAW_ACTIONS)
        for action in CLAW_ACTIONS:
            lines.append(f"/claw {action:<{widths}} - {CLAW_DESCRIPTIONS[action]}")
        self._chat().add_info_message("\n".join(lines))

    # -- Convenience actions (delegating to existing methods) -----------------

    def _action_channels(self) -> None:
        try:
            from vtx_claw.config.schema import CHANNEL_FIELD_NAMES, load_claw_config

            claw_cfg = load_claw_config()
            enabled = [
                name for name in CHANNEL_FIELD_NAMES if getattr(claw_cfg.channels, name).enabled
            ]
            self._chat().add_info_message(
                "[bold]Enabled Channels[/bold]\n"
                + ("\n".join(f"  • {c}" for c in enabled) if enabled else "  None")
            )
        except Exception as e:
            self._chat().add_info_message(f"Failed to load channels: {e}", error=True)

    def _action_skills(self) -> None:
        try:
            from vtx.context.skills import load_skills

            result = load_skills(None)
            skills = result.skills
            if skills:
                lines = ["[bold]Loaded Skills[/bold]"]
                for skill in skills:
                    lines.append(f"  • {skill.name} - {skill.description}")
                self._chat().add_info_message("\n".join(lines))
            else:
                self._chat().add_info_message("[bold]Loaded Skills[/bold]\n  None")
        except Exception as e:
            self._chat().add_info_message(f"Failed to load skills: {e}", error=True)

    def _action_memory(self) -> None:
        try:
            from vtx_claw.config.schema import load_claw_config
            from vtx_claw.memory import MemoryManager

            cfg = load_claw_config()
            manager = MemoryManager(daily_logs=cfg.memory.daily_logs)
            total = sum(len(entries) for entries in manager._entries.values())
            users = list(manager._entries.keys())
            self._chat().add_info_message(
                "[bold]Memory Store[/bold]\n"
                f"  Users: {len(users)}\n"
                f"  Entries: {total}\n"
                f"  Daily logs: {'enabled' if cfg.memory.daily_logs else 'disabled'}"
            )
        except Exception as e:
            self._chat().add_info_message(f"Failed to load memory: {e}", error=True)

    def _action_persona(self) -> None:
        try:
            from vtx_claw.config.schema import load_claw_config
            from vtx_claw.persona import PersonaManager

            cfg = load_claw_config()
            manager = PersonaManager(cfg.persona)
            self._chat().add_info_message(
                "[bold]Persona[/bold]\n"
                f"  Active: {manager.active_name()}\n"
                f"  Available: {', '.join(manager._personas.keys()) or 'default'}"
            )
        except Exception as e:
            self._chat().add_info_message(f"Failed to load persona: {e}", error=True)

    def _action_voice(self) -> None:
        try:
            from vtx_claw.config.schema import load_claw_config

            cfg = load_claw_config()
            status = "[green]Enabled[/green]" if cfg.voice.enabled else "[red]Disabled[/red]"
            self._chat().add_info_message(
                f"[bold]Voice[/bold]\n  Status: {status}\n  Provider: {cfg.llm.provider}"
            )
        except Exception as e:
            self._chat().add_info_message(f"Failed to load voice config: {e}", error=True)

    def _action_sandbox(self) -> None:
        try:
            from vtx_claw.config.schema import load_claw_config

            cfg = load_claw_config()
            status = "[green]Enabled[/green]" if cfg.sandbox.enabled else "[red]Disabled[/red]"
            self._chat().add_info_message(
                "[bold]Sandbox[/bold]\n"
                f"  Status: {status}\n"
                f"  Image: {cfg.sandbox.image}\n"
                f"  Timeout: {cfg.sandbox.timeout_seconds}s"
            )
        except Exception as e:
            self._chat().add_info_message(f"Failed to load sandbox config: {e}", error=True)

    def _action_cron(self) -> None:
        try:
            from vtx_claw.config.schema import load_claw_config

            cfg = load_claw_config()
            status = "[green]Enabled[/green]" if cfg.cron.enabled else "[red]Disabled[/red]"
            jobs = cfg.cron.jobs
            lines = ["[bold]Cron Scheduler[/bold]", f"  Status: {status}", f"  Jobs: {len(jobs)}"]
            for job in jobs:
                if job.enabled:
                    lines.append(f"  • [green]{job.name}[/green] {job.schedule} -> {job.command}")
                else:
                    lines.append(f"  • [red]{job.name}[/red] {job.schedule} -> {job.command}")
            self._chat().add_info_message("\n".join(lines))
        except Exception as e:
            self._chat().add_info_message(f"Failed to load cron config: {e}", error=True)

    def _action_onboard(self) -> None:
        self._chat().add_info_message(
            "To run interactive onboarding, exit the TUI and run:\n[bold]vtx-claw onboard[/bold]"
        )

    # -- Daemon status sync ---------------------------------------------------

    def _update_claw_status(self) -> None:
        """Refresh the claw daemon badge displayed in the info bar."""
        try:
            info_bar = self.query_one("#info-bar", ClawInfoBar)
            info_bar.set_claw_status(_check_daemon_status())
        except Exception:
            pass

    def on_mount(self) -> None:
        """Run Vtx startup logic (runtime init, splash, hooks, binaries, ...)
        then seed the claw daemon status badge.

        ``super().on_mount()`` runs ``Vtx.on_mount()`` which performs all
        critical startup: runtime initialization, session resume, splash
        rendering, hook loading, binary tools setup, update checks, file-path
        scanning, and goal restoration.  Without this call the app would be
        non-functional — no provider, no session, no splash.
        """
        super().on_mount()
        self._update_claw_status()


# ---------------------------------------------------------------------------
# TUI launcher entrypoint (used by cli.py).
# ---------------------------------------------------------------------------


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
        _print_exit_message(hints, session_id, duration, file_changes, program_name="vtx-claw")
