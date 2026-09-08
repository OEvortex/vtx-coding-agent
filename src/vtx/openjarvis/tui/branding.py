"""Branding for OpenJarvis — patches VTX TUI to show OpenJarvis pi-style identity.

Includes gradient block-art logo header, side details, session indicators,
and modern loaded-resource chips.
"""

from __future__ import annotations

import math
import os

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

    accent = config.ui.colors.accent
    dim = config.ui.colors.dim
    muted = config.ui.colors.muted
    success = config.ui.colors.success

    accent_rgb = _hex_to_rgb(accent)
    palette = _build_gradient_palette(accent_rgb, steps=24)

    llm = getattr(config, "llm", None)
    model = (getattr(llm, "default_model", "") or "").strip()
    provider = (getattr(llm, "default_provider", "") or "").strip()
    theme = getattr(getattr(config, "ui", None), "theme", "") or ""
    workspace = os.path.basename(os.getcwd()) or "~"

    info_text = Text()

    # 1. Gradient block-art logo with a refined side info panel
    for row_idx, line in enumerate(_LOGO_LINES):
        span = max(1, len(line) - 1)
        rows = max(1, len(_LOGO_LINES) - 1)
        for col_idx, ch in enumerate(line):
            if ch == " ":
                info_text.append(ch)
            else:
                pos = ((col_idx / span) + (row_idx / rows) * 0.55) * 0.72
                info_text.append(ch, style=_sample_gradient(palette, pos))

        # Side details aligned to specific logo rows
        if row_idx == 0:
            info_text.append("  ", style="")
            info_text.append("●", style=f"bold {accent}")
            info_text.append(" openjarvis", style=f"bold {accent}")
            info_text.append(f"  v{_VERSION}", style=muted)
        elif row_idx == 2:
            info_text.append("  ", style="")
            info_text.append("✦", style=accent)
            info_text.append("  Your personal intelligence", style=muted)
        elif row_idx == 3:
            info_text.append("  ", style="")
            info_text.append("⚡", style=accent)
            info_text.append("  Always on · realtime streaming", style=muted)
        elif row_idx == 5:
            info_text.append("  ", style="")
            info_text.append("◆", style=accent)
            info_text.append("  ", style="")
            if provider or model:
                if model:
                    info_text.append(model, style=f"bold {accent}")
                if provider:
                    info_text.append(f"  {provider}", style=dim)
            else:
                info_text.append("no model configured", style=dim)
        elif row_idx == 6:
            info_text.append("  ", style="")
            info_text.append("⌂", style=accent)
            info_text.append(f"  {workspace}", style=muted)
            if theme:
                info_text.append(f"  ·  {theme}", style=dim)
        elif row_idx == 7:
            info_text.append("  ", style="")
            info_text.append("●", style=f"bold {success}")
            info_text.append(" ready", style=muted)

        info_text.append("\n")

    # 2. Modern keyboard hint chips
    if getattr(config.ui, "show_welcome_shortcuts", True):
        chips = (
            ("/", "commands"),
            ("@", "files"),
            ("!", "bash"),
            ("esc", "interrupt"),
            ("ctrl+c x2", "exit"),
        )
        for key, desc in chips:
            info_text.append(" ", style="")
            info_text.append(f" {key} ", style=f"bold {accent}")
            info_text.append(f" {desc}  ", style=dim)
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
    accent = config.ui.colors.accent

    text = Text()

    if tools:
        text.append("◆ ", style=f"bold {accent}")
        text.append("Tools", style=f"bold {accent}")
        text.append(f"  {len(tools)}", style=muted)
        text.append("\n")
        for i, tool in enumerate(tools):
            last = i == len(tools) - 1
            text.append("  ╰─ " if last else "  ├─ ", style=dim)
            icon = _tool_icon(getattr(tool, "name", ""), tool)
            text.append(f"{icon} ", style=accent)
            text.append(str(getattr(tool, "name", "")), style=muted)
            if not last:
                text.append("\n")

    if context_paths:
        if tools:
            text.append("\n")
        text.append("◆ ", style=f"bold {accent}")
        text.append("Context", style=f"bold {accent}")
        text.append(f"  {len(context_paths)}", style=muted)
        text.append("\n")
        for i, path in enumerate(context_paths):
            last = i == len(context_paths) - 1
            text.append("  ╰─ " if last else "  ├─ ", style=dim)
            text.append(str(path), style=muted)
            if not last:
                text.append("\n")

    if skills:
        if tools or context_paths:
            text.append("\n")
        text.append("◆ ", style=f"bold {accent}")
        text.append("Skills", style=f"bold {accent}")
        text.append(f"  {len(skills)}", style=muted)
        text.append("\n  ╰─ ", style=dim)
        text.append(", ".join(_SKILL_LABEL(skill) for skill in skills), style=muted)

    text.rstrip()
    label = Label(text)
    label.add_class("info-message")
    label.add_class("loaded-resources")
    self.mount(label)


_chat.ChatLog.add_session_info = _patched_add_session_info  # type: ignore
_chat.ChatLog.add_loaded_resources = _patched_add_loaded_resources  # type: ignore

# ---------------------------------------------------------------------------
# Monkey-patch ToolBlock for seamless pi-style boxed and quiet tool rendering
# ---------------------------------------------------------------------------

try:
    from vtx.tui.blocks import ToolBlock as _ToolBlock
except Exception:
    _ToolBlock = None  # ty:ignore[invalid-assignment]

if _ToolBlock is not None:
    _ORIGINAL_FORMAT_HEADER = _ToolBlock._format_header
    _ORIGINAL_RENDER_RESULT_OUTPUT = _ToolBlock._render_result_output

    def _patched_format_header(self: _ToolBlock, truncate: bool = True) -> Text:
        # 1. If result is a boxed card (starts with ╭─), the boxed card carries
        # its own title, state, command, and top border; suppress outer text header.
        if self._ui_details and self._ui_details.lstrip().startswith("╭─"):
            return Text("")

        # 2. If ui_summary is already a full formatted header (e.g. "● Read ..."),
        # render it directly without prepending the raw tool name / icon.
        if self._ui_summary:
            first_ch = self._ui_summary.strip()[:1]
            if first_ch in ("●", "Q", "🌐", "λ", "◈", "◉", "⏱", "✉", "↳", "▦", "?", "✓", "✗"):
                return self._render_markup_safe(self._ui_summary)

        return _ORIGINAL_FORMAT_HEADER(self, truncate=truncate)

    def _patched_render_result_output(self: _ToolBlock) -> None:
        _ORIGINAL_RENDER_RESULT_OUTPUT(self)
        # If output is a full boxed card, hide the redundant header label
        try:
            header_label = self.query_one("#tool-header", Label)
            if self._ui_details and self._ui_details.lstrip().startswith("╭─"):
                header_label.add_class("-hidden")
            else:
                header_label.remove_class("-hidden")
        except Exception:
            pass

    _ToolBlock._format_header = _patched_format_header  # type: ignore
    _ToolBlock._render_result_output = _patched_render_result_output  # type: ignore
