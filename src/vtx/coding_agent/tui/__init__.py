"""The coding agent's Textual interface: app shell, chat pane, panels, dialogs,
and the slash-command registry.

Built on the base toolkit in :mod:`vtx.tui`, which owns the reusable
primitives (input, blocks, fuzzy lists, styling). Nothing in the base package
imports this module — the dependency runs one way, base to product.
"""

from __future__ import annotations

__all__ = [
    "ChatLog",
    "CommandsMixin",
    "InfoBar",
    "QueueDisplay",
    "StatusLine",
    "TreeSelector",
    "Vtx",
    "export_session_html",
    "format_path",
    "run_tui",
]

_LAZY_MAP = {
    "Vtx": ".app",
    "run_tui": ".launch",
    "ChatLog": ".chat",
    "InfoBar": ".widgets",
    "StatusLine": ".widgets",
    "QueueDisplay": ".widgets",
    "format_path": ".widgets",
    "TreeSelector": ".tree",
    "CommandsMixin": ".commands",
    "export_session_html": ".export",
}


def __getattr__(name: str):
    if name in _LAZY_MAP:
        from importlib import import_module

        mod = import_module(_LAZY_MAP[name], __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
