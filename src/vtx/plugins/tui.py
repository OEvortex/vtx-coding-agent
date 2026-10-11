"""Installing the TUI's interactive channel into the plugin composition.

The TUI is a UI *provider*, not a special case: it implements the ``notify``
and ``ask`` surface a plugin sees through ``ctx.ui``, and it publishes the
built-in slash commands through ``ctx.commands`` so a plugin can list them
without importing the autocomplete table.

Neither direction changes how the TUI dispatches a slash command today — this
is a read/write window onto the running composition, which is what a plugin
needs in order to be a peer of the built-ins.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("vtx.plugins.tui")

#: The built-in commands and their one-line descriptions, in menu order. Kept
#: as data so a plugin can enumerate them without importing the app.
BUILTIN_COMMANDS: tuple[tuple[str, str], ...] = (
    ("help", "show available commands"),
    ("quit", "quit the application"),
    ("clear", "clear conversation history"),
    ("model", "change model"),
    ("provider", "filter /model by provider"),
    ("settings", "themes, permissions, thinking, notifications"),
    ("ponytail", "toggle or set ponytail mode (on/off)"),
    ("new", "start new conversation"),
    ("handoff", "start focused handoff in new session"),
    ("resume", "resume a session"),
    ("tree", "navigate session tree"),
    ("undo", "revert the last turn and its file changes"),
    ("redo", "step forward again after an /undo"),
    ("session", "show session info and stats"),
    ("usage", "token usage: this session, 30 days, lifetime"),
    ("login", "login to a provider"),
    ("logout", "logout from a provider"),
    ("export", "export session to HTML"),
    ("copy", "copy last agent response text"),
    ("compact", "compact now; /compact <focus> steers the summary"),
    ("recap", "summarize where the session left off"),
    ("agent", "list/switch handoff agents (Shift+Tab to cycle)"),
    ("update", "check for and install latest vtx update"),
    ("reload", "re-apply config, extensions, agents, tools, skills"),
    ("mcp", "list/manage MCP servers (/mcp, /mcp reload, /mcp signin <name>)"),
)


class TUIChannel:
    """The UI implementation the TUI installs on ``ctx.ui``.

    Every method is safe to call from a plugin running in the agent's event
    loop: ``notify`` schedules onto the Textual app, and a failure to schedule
    is logged rather than raised, because a plugin that broke must not take the
    session down with it.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    def notify(self, message: str, level: str = "info") -> None:
        add = getattr(self._app, f"add_{level}_message", None) or getattr(
            self._app, "add_info_message", None
        )
        if add is None:
            log.debug("no UI sink for %s: %s", level, message)
            return
        try:
            add(message)
        except Exception:  # pragma: no cover - a broken sink must not propagate
            log.exception("ui.notify failed")

    def ask(self, question: str, options: Any = None) -> str | None:
        """Ask the person a question in the chat log.

        Returns ``None`` when the app cannot pose one: a plugin that asked
        during shutdown, or without a session, gets no answer rather than a
        fabricated one.
        """
        ask = getattr(self._app, "ask_user", None)
        if ask is None:
            return None
        try:
            return ask(question, options)
        except Exception:  # pragma: no cover
            log.exception("ui.ask failed")
            return None


def install(app: Any, host: Any) -> None:
    """Wire ``app`` into the composition ``host`` booted.

    Safe to call when no composition is mounted, and safe to call twice: the
    second call replaces the channel rather than stacking a second one.
    """
    if host is None:
        return
    ctx = host.base
    if "ui" in ctx:
        ctx.ui.set_context(TUIChannel(app))
    if "commands" in ctx:
        commands = ctx.commands
        for name, description in BUILTIN_COMMANDS:
            if name not in commands:
                # A descriptor, not a dispatch: the TUI still routes its own
                # commands, and this only makes the list readable from a plugin.
                commands.register(name, lambda *_a, **_k: None, description)


def uninstall(host: Any) -> None:
    """Drop the TUI's channel and the descriptors it published."""
    if host is None:
        return
    ctx = host.base
    if "ui" in ctx:
        ctx.ui.set_context(None)
    if "commands" in ctx:
        for name, _description in BUILTIN_COMMANDS:
            ctx.commands.unregister(name)


__all__ = ["BUILTIN_COMMANDS", "TUIChannel", "install", "uninstall"]
