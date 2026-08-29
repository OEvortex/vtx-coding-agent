"""TUI launch for OpenJarvis."""

from __future__ import annotations

import argparse
import contextlib

from rich.console import Console

from vtx.openjarvis.tui.app import OpenJarvisApp

_LOGO = [
    "  ___  ___  ___  _  ___  ___  ___ ",
    " / _ \\| _ \\| __|| |/ __|| _ \\/ __|",
    "| (_) |  _/| _| | | (__ |   /\\__ \\",
    " \\___/|_|  |___||_|\\___||_|_\\|___/",
]


def _print_exit(hints: list[str], session_id: str | None, duration: float | None) -> None:
    console = Console(highlight=False)
    for h in hints:
        console.print(f"[dim]Hint: {h}[/dim]")
    if session_id:
        console.print(f"[dim]To resume: vtx-jarvis tui --resume {session_id}[/dim]")
    if duration is not None:
        console.print(f"[dim]Time {int(duration)}s[/dim]")
    console.print()
    for line in _LOGO:
        console.print(f"[dim]{line}[/dim]")
    console.print()


def run_tui(args: argparse.Namespace) -> None:
    app = OpenJarvisApp(cwd=getattr(args, "cwd", None), model=getattr(args, "model", None))
    app.run()
    hints: list[str] = []
    with contextlib.suppress(Exception):
        _print_exit(hints, None, None)
