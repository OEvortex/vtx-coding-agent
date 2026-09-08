"""TUI launch for OpenJarvis."""

from __future__ import annotations

import argparse
import contextlib
import math

from rich.console import Console
from rich.text import Text

from vtx.openjarvis.tui.app import OpenJarvisApp

_WORDMARK = (
    " ██████╗ ██████╗ ███████╗███╗   ██╗      █████╗ ██████╗ ██╗   ██╗██╗███████╗",
    "██╔═══██╗██╔══██╗██╔════╝████╗  ██║     ██╔══██╗██╔══██╗╚██╗ ██╔╝██║██╔════╝",
    "██║   ██║██████╔╝███████╗██╔██╗ ██║     ███████║██████╔╝ ╚████╔╝ ██║███████╗",
    "██║   ██║██╔══██║╚════██║██║╚██╗██║     ██╔══██║██╔══██╗  ╚██╔╝  ██║╚════██║",
    "╚██████╔╝██████╔╝███████║██║ ╚████║██╗  ██║  ██║██║  ██║   ██║   ██║███████║",
    " ╚═════╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝╚═╝  ╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝╚══════╝",
)


def _accent_hex() -> str:
    with contextlib.suppress(Exception):
        from vtx.ai.config import config

        accent = getattr(getattr(config, "ui", None), "colors", None)
        accent = getattr(accent, "accent", None)
        if isinstance(accent, str) and accent.startswith("#"):
            return accent
    return "#00b4ff"


def _gradient_wordmark(console: Console) -> None:
    """Print the exit wordmark with a diagonal accent gradient."""
    hex_code = _accent_hex().lstrip("#")
    try:
        r, g, b = int(hex_code[0:2], 16), int(hex_code[2:4], 16), int(hex_code[4:6], 16)
    except (ValueError, IndexError):
        r, g, b = 0, 180, 255

    text = Text()
    rows = len(_WORDMARK) - 1
    for row_idx, line in enumerate(_WORDMARK):
        span = max(1, len(line) - 1)
        for col_idx, ch in enumerate(line):
            if ch == " ":
                text.append(ch)
            else:
                progress = (col_idx / span) + (row_idx / rows) * 0.5
                wave = -math.cos(progress * math.pi)
                rr = round(r + (255 - r) * max(0.0, wave * 0.25))
                gg = round(g + (255 - g) * max(0.0, wave * 0.25))
                bb = round(b + (255 - b) * max(0.0, wave * 0.25))
                text.append(ch, style=f"#{rr:02x}{gg:02x}{bb:02x}")
        text.append("\n")
    text.rstrip()
    console.print(text)


def _print_exit(hints: list[str], session_id: str | None, duration: float | None) -> None:
    console = Console(highlight=False)
    console.print()
    _gradient_wordmark(console)

    lines: list[tuple[str, str]] = []
    if duration is not None:
        mins, secs = divmod(int(duration), 60)
        elapsed = f"{mins}m {secs}s" if mins else f"{secs}s"
        lines.append(("⏱", f"session lasted {elapsed}"))
    if session_id:
        lines.append(("↺", f"resume with: openjarvis tui --resume {session_id}"))
    for h in hints:
        lines.append(("✦", h))
    lines.append(("◆", "openjarvis · your personal intelligence"))

    icon_w = max(len(icon) for icon, _ in lines)
    for icon, msg in lines:
        console.print(f"  [dim]{icon:<{icon_w}}[/dim]  [dim]{msg}[/dim]")
    console.print()


def run_tui(args: argparse.Namespace) -> None:
    app = OpenJarvisApp(cwd=getattr(args, "cwd", None), model=getattr(args, "model", None))
    app.run()
    hints: list[str] = []
    with contextlib.suppress(Exception):
        _print_exit(hints, None, None)
