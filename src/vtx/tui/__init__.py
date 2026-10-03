"""Vtx TUI — the Textual interface.

One flat package holding the whole interactive UI: the reusable primitives
(input editing, fuzzy matching, overlay lists, LaTeX/formatting helpers,
styling, block renderers) and the app that composes them (``app.Vtx``, the
agent-runner mixins, the panels, and the slash-command registry).

Anything that renders a terminal conversation can reuse the primitives alone
without pulling in the app. The agent harness already depends on this package
for ``blocks.TaskToolBlock`` and tool-output truncation.
"""

from __future__ import annotations

__all__ = [
    "DEFAULT_COMMANDS",
    "WITTY_STATUS_LINES",
    "AgentRunnerMixin",
    "AgentsPanel",
    "AskUserDialog",
    "AutocompleteProvider",
    "ChatLog",
    "CommandSupport",
    "CompletionUIMixin",
    "ContentBlock",
    "FileChangesModal",
    "FilePathProvider",
    "FloatingList",
    "GoalWidget",
    "HandoffLinkBlock",
    "InfoBar",
    "InputBox",
    "LaunchWarning",
    "LaunchWarningsBlock",
    "ListItem",
    "PullRequestProvider",
    "QueueDisplay",
    "QueueUIMixin",
    "RecapMixin",
    "SelectionMode",
    "SessionUIMixin",
    "SlashCommand",
    "StartupMixin",
    "StatusLine",
    "TextualExtensionUI",
    "ThinkingBlock",
    "ToolBlock",
    "TreeSelector",
    "UpdateAvailableBlock",
    "UserBlock",
    "Vtx",
    "export_session_html",
    "format_path",
    "format_tokens",
    "get_git_branch",
    "get_styles",
    "pick_witty_line",
    "preprocess_latex",
    "run_tui",
    "stylize_badge_markers",
    "subagents_own_the_status_line",
]

_LAZY_MAP = {
    "InputBox": ".input",
    "FloatingList": ".floating_list",
    "ListItem": ".floating_list",
    "ContentBlock": ".blocks",
    "HandoffLinkBlock": ".blocks",
    "LaunchWarning": ".blocks",
    "Vtx": ".app",
    "run_tui": ".launch",
    "AgentRunnerMixin": ".agent_runner",
    "CompletionUIMixin": ".completion_ui",
    "RecapMixin": ".recap",
    "SessionUIMixin": ".session_ui",
    "StartupMixin": ".startup",
    "ChatLog": ".chat",
    "AgentsPanel": ".agents_panel",
    "GoalWidget": ".goal_ui",
    "FileChangesModal": ".widgets",
    "InfoBar": ".widgets",
    "QueueDisplay": ".widgets",
    "StatusLine": ".widgets",
    "format_path": ".widgets",
    "get_git_branch": ".widgets",
    "QueueUIMixin": ".queue_ui",
    "TreeSelector": ".tree",
    "CommandSupport": ".commands.base",
    "WITTY_STATUS_LINES": ".status_lines",
    "pick_witty_line": ".status_lines",
    "subagents_own_the_status_line": ".status_lines",
    "TextualExtensionUI": ".extension_ui",
    "export_session_html": ".export",
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
