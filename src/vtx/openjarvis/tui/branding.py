"""Branding for OpenJarvis — patches VTX TUI to show OpenJarvis identity."""

from __future__ import annotations

try:
    import vtx.tui.chat as _chat
    import vtx.tui.styles as _styles
    from vtx.coding_agent.config import config
    from vtx.tui.blocks import ToolBlock
except (ModuleNotFoundError, ImportError):
    try:
        import vtx.ui.chat as _chat  # type: ignore
        from vtx.config import get_config  # type: ignore

        config = get_config()
    except Exception:
        from vtx.openjarvis.config import config  # type: ignore

from rich.text import Text
from textual.widgets import Label

from vtx.openjarvis.version import VERSION as _VERSION

_ORIGINAL_ADD_SESSION_INFO = _chat.ChatLog.add_session_info


def _patched_add_session_info(self: _chat.ChatLog, version: str) -> None:
    info_text = Text()
    accent = config.ui.colors.accent
    dim = config.ui.colors.dim

    logo_lines = ("░█░█░███░█░█", "░█░█░░█░░░█░", "░░█░░░█░░█░█")
    for i, line in enumerate(logo_lines):
        info_text.append(line, style=accent)
        if i == len(logo_lines) - 1:
            info_text.append(f" openjarvis v{_VERSION}", style=dim)
        info_text.append("\n")
    info_text.append("  Autonomous Agent • VTX-native", style=dim)
    info_text.append("\n")
    info_text.append("  [", style=dim)
    info_text.append("●", style=config.ui.colors.success)
    info_text.append(" gateway", style=dim)
    info_text.append("  ", style=dim)
    info_text.append("●", style=config.ui.colors.notice)
    info_text.append(" channels", style=dim)
    info_text.append("  ", style=dim)
    info_text.append("●", style=config.ui.colors.accent)
    info_text.append(" cron", style=dim)
    info_text.append("]", style=dim)
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
            for item_idx, (key, desc) in enumerate(row):
                if item_idx > 0:
                    info_text.append(" • ", style=dim)
                info_text.append(key, style=config.ui.colors.muted)
                info_text.append(f" {desc}", style=dim)
            if row_idx < len(shortcut_rows) - 1:
                info_text.append("\n")
    info_text.rstrip()
    info_label = Label(info_text)
    info_label.add_class("session-info")
    self.mount(info_label, before=0)


_chat.ChatLog.add_session_info = _patched_add_session_info  # type: ignore[method-assign]
