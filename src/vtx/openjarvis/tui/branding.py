"""Branding for OpenJarvis — patches VTX TUI to show OpenJarvis pi-style identity.

Includes gradient block-art logo header, side details, session indicators,
and modern loaded-resource chips.
"""

from __future__ import annotations

import math

try:
    import vtx.tui.chat as _chat
    import vtx.tui.styles as _styles  # noqa: F401
    from vtx.coding_agent.config import config
    from vtx.tui.blocks import ToolBlock  # noqa: F401
except (ModuleNotFoundError, ImportError):
    try:
        import vtx.ui.chat as _chat  # type: ignore
        from vtx.config import get_config  # type: ignore

        config = get_config()
    except Exception:
        from vtx.openjarvis.config import config  # type: ignore

from rich.text import Text
from textual.widgets import Label

from vtx.openjarvis.tui.tool_ui import tool_icon as _tool_icon
from vtx.openjarvis.version import VERSION as _VERSION

_ORIGINAL_ADD_SESSION_INFO = _chat.ChatLog.add_session_info
_ORIGINAL_ADD_LOADED_RESOURCES = _chat.ChatLog.add_loaded_resources
_SKILL_LABEL = getattr(_chat, "_format_skill_label", None) or (lambda skill: skill.name)

# 9-line block-art logo (OpenJarvis OJ symbol)
_LOGO_LINES = (
    "██████████╗   ██████████╗",
    "██████████║   ╚═════████║",
    "████╔══████╗        ████║",
    "████║  ████║        ████║",
    "████║  ████║        ████║",
    "████║  ████║        ████║",
    "████║  ████║  ████╗ ████║",
    "████╚══████║  ████╚═████║",
    "╚██████████╝  ╚████████╔╝",
)


def _hex_to_rgb(hex_code: str) -> tuple[int, int, int]:
    h = hex_code.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except Exception:
        return (0, 180, 255)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"


def _build_gradient_palette(
    accent_rgb: tuple[int, int, int], steps: int = 24
) -> list[tuple[int, int, int]]:
    palette: list[tuple[int, int, int]] = []
    r, g, b = accent_rgb
    for i in range(steps):
        progress = i / steps
        wave = -math.cos(progress * math.pi * 2)
        if wave < 0:
            factor = 1.0 - (0.18 * -wave)
            palette.append((round(r * factor), round(g * factor), round(b * factor)))
        else:
            factor = 0.18 * wave
            palette.append(
                (
                    round(r + (255 - r) * factor),
                    round(g + (255 - g) * factor),
                    round(b + (255 - b) * factor),
                )
            )
    return palette


def _sample_gradient(palette: list[tuple[int, int, int]], pos: float) -> str:
    wrapped = ((pos % 1.0) + 1.0) % 1.0
    scaled = wrapped * len(palette)
    idx1 = int(scaled) % len(palette)
    idx2 = (idx1 + 1) % len(palette)
    frac = scaled - int(scaled)
    r1, g1, b1 = palette[idx1]
    r2, g2, b2 = palette[idx2]
    r = round(r1 + (r2 - r1) * frac)
    g = round(g1 + (g2 - g1) * frac)
    b = round(b1 + (b2 - b1) * frac)
    return _rgb_to_hex(r, g, b)


def _patched_add_session_info(self: _chat.ChatLog, version: str) -> None:
    # Clear any previous session-info to avoid duplicate logos
    for child in list(self.children):
        if hasattr(child, "has_class") and child.has_class("session-info"):
            child.remove()

    info_text = Text()
    accent = config.ui.colors.accent
    dim = config.ui.colors.dim
    muted = config.ui.colors.muted
    success = config.ui.colors.success

    accent_rgb = _hex_to_rgb(accent)
    palette = _build_gradient_palette(accent_rgb, steps=24)

    # 1. Gradient block-art logo with side details
    for row_idx, line in enumerate(_LOGO_LINES):
        span = max(1, len(line) - 1)
        for col_idx, ch in enumerate(line):
            if ch == " ":
                info_text.append(ch)
            else:
                pos = (col_idx / span) + (row_idx * 0.12)
                color = _sample_gradient(palette, pos)
                info_text.append(ch, style=color)

        # Side details on specific rows
        if row_idx == 0:
            info_text.append("   ■ openjarvis", style=f"bold {accent}")
        elif row_idx == 1:
            info_text.append("   / commands · ! bash", style=dim)
        elif row_idx == 2:
            info_text.append("   ")
            info_text.append("●", style=f"bold {success}")
            info_text.append(" ready", style=muted)
        elif row_idx == 3:
            info_text.append(f"   openjarvis v{_VERSION} · VTX-native", style=dim)

        info_text.append("\n")

    # 2. New session started badge
    info_text.append("\n")
    info_text.append("✦ ", style=accent)
    info_text.append("New session started", style=f"{accent}")
    info_text.append("\n")

    info_text.rstrip()
    info_label = Label(info_text)
    info_label.add_class("session-info")
    self.mount(info_label, before=0)


def _patched_add_loaded_resources(
    self: _chat.ChatLog, context_paths: list[str], skills: list, tools: list
) -> None:
    """Jarvis-style resource banner: iconified tools, clean sections."""
    if not context_paths and not skills and not tools:
        return

    # Clear any previous loaded-resources
    for child in list(self.children):
        if hasattr(child, "has_class") and child.has_class("loaded-resources"):
            child.remove()

    dim = config.ui.colors.dim
    muted = config.ui.colors.muted
    notice_color = config.ui.colors.notice
    accent = config.ui.colors.accent

    text = Text()

    if tools:
        text.append("◆ Resources\n", style=f"bold {accent}")
        text.append("  [Tools] ", style=notice_color)
        for i, tool in enumerate(tools):
            if i:
                text.append("  ", style=dim)
            icon = _tool_icon(getattr(tool, "name", ""), tool)
            text.append(icon, style=accent)
            text.append(f" {getattr(tool, 'name', '')}", style=dim)
        text.append("\n")

    if context_paths:
        text.append("  [Context] ", style=notice_color)
        for i, path in enumerate(context_paths):
            if i:
                text.append(" · ", style=dim)
            text.append(f"{path}", style=dim)
        text.append("\n")

    if skills:
        text.append("  [Skills] ", style=notice_color)
        text.append(", ".join(_SKILL_LABEL(skill) for skill in skills), style=muted)
        text.append("\n")

    text.rstrip()
    label = Label(text)
    label.add_class("info-message")
    label.add_class("loaded-resources")
    self.mount(label)


_chat.ChatLog.add_session_info = _patched_add_session_info  # type: ignore
_chat.ChatLog.add_loaded_resources = _patched_add_loaded_resources  # type: ignore
