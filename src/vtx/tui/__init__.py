"""Vtx TUI — the base terminal-UI toolkit.

Reusable Textual primitives with no knowledge of the agent, the model
catalog, sessions, goals, or slash commands: input editing, fuzzy matching,
overlay lists, LaTeX/formatting helpers, styling, and the block renderers for
tool calls and results.

This is a **base** package. It never imports :mod:`vtx.coding_agent`, so the
agent harness can depend on it (see ``blocks.TaskToolBlock``). The coding
agent's own screens and panels live in :mod:`vtx.coding_agent.tui`.
"""

from __future__ import annotations

__all__ = [
    "DEFAULT_COMMANDS",
    "AskUserDialog",
    "AutocompleteProvider",
    "ContentBlock",
    "FilePathProvider",
    "FloatingList",
    "HandoffLinkBlock",
    "InputBox",
    "LaunchWarning",
    "LaunchWarningsBlock",
    "ListItem",
    "PullRequestProvider",
    "SelectionMode",
    "SlashCommand",
    "ThinkingBlock",
    "ToolBlock",
    "UpdateAvailableBlock",
    "UserBlock",
    "format_tokens",
    "get_styles",
    "preprocess_latex",
    "stylize_badge_markers",
]

_LAZY_MAP = {
    "InputBox": ".input",
    "FloatingList": ".floating_list",
    "ListItem": ".floating_list",
    "ContentBlock": ".blocks",
    "HandoffLinkBlock": ".blocks",
    "LaunchWarning": ".blocks",
    "LaunchWarningsBlock": ".blocks",
    "ThinkingBlock": ".blocks",
    "ToolBlock": ".blocks",
    "UpdateAvailableBlock": ".blocks",
    "UserBlock": ".blocks",
    "stylize_badge_markers": ".blocks",
    "AskUserDialog": ".ask_user",
    "DEFAULT_COMMANDS": ".autocomplete",
    "SlashCommand": ".autocomplete",
    "AutocompleteProvider": ".autocomplete",
    "FilePathProvider": ".autocomplete",
    "PullRequestProvider": ".autocomplete",
    "SelectionMode": ".selection_mode",
    "format_tokens": ".formatting",
    "get_styles": ".styles",
    "preprocess_latex": ".latex",
}


def __getattr__(name: str):
    if name in _LAZY_MAP:
        from importlib import import_module

        mod = import_module(_LAZY_MAP[name], __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
