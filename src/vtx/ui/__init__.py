"""Vtx TUI — public surface for embedding or extending the Textual interface."""

from __future__ import annotations

__all__ = [
    "DEFAULT_COMMANDS",
    "ChatLog",
    "CommandsMixin",
    "ContentBlock",
    "FloatingList",
    "HandoffLinkBlock",
    "InfoBar",
    "InputBox",
    "LaunchWarning",
    "LaunchWarningsBlock",
    "ListItem",
    "QueueDisplay",
    "SelectionMode",
    "SlashCommand",
    "StatusLine",
    "ThinkingBlock",
    "ToolBlock",
    "TreeSelector",
    "UpdateAvailableBlock",
    "UserBlock",
    "Vtx",
    "export_session_html",
    "format_path",
    "format_tokens",
    "get_styles",
    "preprocess_latex",
    "run_tui",
    "stylize_badge_markers",
]

_LAZY_MAP = {
    "Vtx": ".app",
    "run_tui": ".launch",
    "ChatLog": ".chat",
    "InputBox": ".input",
    "InfoBar": ".widgets",
    "StatusLine": ".widgets",
    "QueueDisplay": ".widgets",
    "format_path": ".widgets",
    "FloatingList": ".floating_list",
    "ListItem": ".floating_list",
    "TreeSelector": ".tree",
    "ContentBlock": ".blocks",
    "HandoffLinkBlock": ".blocks",
    "LaunchWarning": ".blocks",
    "LaunchWarningsBlock": ".blocks",
    "ThinkingBlock": ".blocks",
    "ToolBlock": ".blocks",
    "UpdateAvailableBlock": ".blocks",
    "UserBlock": ".blocks",
    "stylize_badge_markers": ".blocks",
    "CommandsMixin": ".commands",
    "SelectionMode": ".selection_mode",
    "format_tokens": ".formatting",
    "get_styles": ".styles",
    "preprocess_latex": ".latex",
    "export_session_html": ".export",
    "DEFAULT_COMMANDS": ".autocomplete",
    "SlashCommand": ".autocomplete",
}


def __getattr__(name: str):
    if name in _LAZY_MAP:
        from importlib import import_module

        mod = import_module(_LAZY_MAP[name], __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
