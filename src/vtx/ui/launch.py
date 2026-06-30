"""TUI entrypoint and the exit summary printed after the app closes."""

from __future__ import annotations

import argparse
import time

from rich.console import Console

from vtx import config

from .app import Vtx

_LOGO = [
    "██╗   ██╗████████╗██╗  ██╗",
    "██║   ██║╚══██╔══╝╚██╗██╔╝",
    "██║   ██║   ██║    ╚███╔╝ ",
    "╚██╗ ██╔╝   ██║    ██╔██╗ ",
    " ╚████╔╝    ██║   ██╔╝ ██╗",
    "  ╚═══╝     ╚═╝   ╚═╝  ╚═╝",
]


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    minutes = total // 60
    secs = total % 60
    return f"{minutes}m {secs}s"


def _print_exit_message(
    hints: list[str],
    session_id: str | None = None,
    duration_seconds: float | None = None,
    file_changes: dict[str, tuple[int, int]] | None = None,
    program_name: str = "vtx",
) -> None:
    colors = config.ui.colors
    console = Console(highlight=False)

    for hint in hints:
        console.print(
            f"[{colors.muted}]Hint:[/{colors.muted}] [{colors.dim}]{hint}[/{colors.dim}]"
        )

    t = colors.dim
    logo_color = colors.dim
    info_lines: list[str] = []

    if duration_seconds is not None:
        info_lines.append(f"[{t}]Time {_format_duration(duration_seconds)}[/{t}]")

    if file_changes:
        n_files = len(file_changes)
        total_added = sum(a for a, _ in file_changes.values())
        total_removed = sum(r for _, r in file_changes.values())
        info_lines.append(
            f"[{t}]Changed {n_files} file{'s' if n_files != 1 else ''}[/{t}]"
            f" [{colors.diff_added}]+{total_added}[/{colors.diff_added}]"
            f" [{colors.diff_removed}]-{total_removed}[/{colors.diff_removed}]"
        )

    if session_id:
        info_lines.append(
            f"[{colors.muted}]To resume:[/{colors.muted}] "
            f"[{colors.accent}]{program_name} -r {session_id}[/{colors.accent}]"
        )

    if not info_lines:
        return

    while len(info_lines) < len(_LOGO):
        info_lines.append("")

    console.print()
    for logo_line, info_line in zip(_LOGO, info_lines, strict=False):
        padding = "  " if info_line else ""
        console.print(f"  [{logo_color}]{logo_line}[/{logo_color}]{padding}{info_line}")
    console.print()


def run_tui(args: argparse.Namespace) -> None:
    app = Vtx(
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
    # Sync emit so we don't have to spin up another event loop.
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
