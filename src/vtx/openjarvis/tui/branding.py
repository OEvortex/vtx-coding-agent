"""Branding for OpenJarvis — patches VTX TUI to show OpenJarvis identity.

Also modernizes the loaded-resources banner (tools/skills/context) with
Jarvis-style icons, reusing vtx.tui's own config colors and widget classes.
"""

from __future__ import annotations

try:
    import vtx.tui.chat as _chat
    import vtx.tui.styles as _styles  # noqa: F401  (availability probe, legacy path)
    from vtx.coding_agent.config import config
    from vtx.tui.blocks import ToolBlock  # noqa: F401  (availability probe, legacy path)
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


def _patched_add_session_info(self: _chat.ChatLog, version: str) -> None:
    info_text = Text()
    accent = config.ui.colors.accent
    dim = config.ui.colors.dim
    muted = config.ui.colors.muted
    success = config.ui.colors.success
    notice = config.ui.colors.notice

    # Arc-reactor logo + title block
    logo_lines = ("░█░█░███░█░█", "░█░█░░█░░░█░", "░░█░░░█░░█░█")
    for i, line in enumerate(logo_lines):
        info_text.append(line, style=accent)
        if i == 0:
            info_text.append("  J.A.R.V.I.S", style=f"bold {accent}")
        elif i == 1:
            info_text.append(f"  openjarvis v{_VERSION}", style=muted)
        elif i == 2:
            info_text.append("  Autonomous Agent · VTX-native", style=dim)
        info_text.append("\n")

    # System status chips
    chips = (("◉", "gateway", success), ("✉", "channels", notice), ("⏱", "cron", accent))
    info_text.append("  ", style=dim)
    for i, (icon, label, color) in enumerate(chips):
        if i:
            info_text.append("  ·  ", style=dim)
        info_text.append(icon, style=color)
        info_text.append(f" {label}", style=dim)
    info_text.append("\n")

    if config.ui.show_welcome_shortcuts:
        info_text.append("\n")
        shortcut_rows = (
            (
                ("/", "slash commands"),
                ("@", "files/dirs"),
                ("tab", "complete"),
                ("↑/↓", "history"),
            ),
            (("shift+tab", "switch agent"), ("esc", "interrupt"), ("shift+enter", "newline")),
            (
                ("ctrl+c", "clear"),
                ("ctrl+c x2", "exit"),
                ("enter", "send"),
                ("ctrl+t", "thinking"),
            ),
        )
        for row_idx, row in enumerate(shortcut_rows):
            info_text.append("  ", style=dim)
            for item_idx, (key, desc) in enumerate(row):
                if item_idx > 0:
                    info_text.append(" • ", style=dim)
                info_text.append(key, style=muted)
                info_text.append(f" {desc}", style=dim)
            if row_idx < len(shortcut_rows) - 1:
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

    dim = config.ui.colors.dim
    muted = config.ui.colors.muted
    notice_color = config.ui.colors.notice

    text = Text()

    if tools:
        text.append("[Tools]\n", style=notice_color)
        text.append("  ", style=dim)
        for i, tool in enumerate(tools):
            if i:
                text.append("  ", style=dim)
            icon = _tool_icon(getattr(tool, "name", ""), tool)
            text.append(icon, style=config.ui.colors.accent)
            text.append(f" {tool.name}", style=dim)
        text.append("\n")

    if context_paths:
        if tools:
            text.append("\n")
        text.append("[Context]\n", style=notice_color)
        for path in context_paths:
            text.append(f"  · {path}\n", style=dim)

    if skills:
        if context_paths or tools:
            text.append("\n")
        text.append("[Skills]\n", style=notice_color)
        text.append("  ", style=dim)
        text.append(", ".join(_SKILL_LABEL(skill) for skill in skills), style=muted)
        text.append("\n", style=dim)

    text.rstrip()
    label = Label(text)
    label.add_class("info-message")
    label.add_class("loaded-resources")
    self.mount(label)


_chat.ChatLog.add_session_info = _patched_add_session_info  # type: ignore
_chat.ChatLog.add_loaded_resources = _patched_add_loaded_resources  # type: ignore
