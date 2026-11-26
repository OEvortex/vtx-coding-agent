"""Slash-command dispatch for vx.

The verb set, arguments and user-facing wording are ported from
``packages/code/src/ctx/term/commands`` so the two front ends behave the same.
What differs is the surface: vx answers with a transcript block, a footer
picker, or the composer used as a one-line prompt.

- settings.py — /settings, /themes, /permissions, /thinking, /effort,
  /notifications
- models.py   — /model, /provider
- sessions.py — /new, /clear, /tree, /session, /handoff, /compact,
  /export, /copy, /undo, /redo, /usage, /recap
- agents.py   — /agent, /switch
- auth.py     — /login, /logout
- system.py   — /skills, /tools, /mcp, /reload, /update
"""

from __future__ import annotations

from typing import Any

from vx.commands.agents import AgentCommands
from vx.commands.auth import AuthCommands
from vx.commands.base import CommandSupport
from vx.commands.models import ModelCommands
from vx.commands.sessions import SessionCommands
from vx.commands.settings import SettingsCommands
from vx.commands.system import SystemCommands
from vx.tui import blocks, footer

#: name, description, presentation category. fx groups commands this way and
#: vx shows the category in the picker's metadata column.
SLASH_COMMANDS: list[tuple[str, str, str]] = [
    ("/help", "show commands and key bindings", "General"),
    ("/find", "search the transcript", "General"),
    ("/model", "choose the model, or refresh the catalog", "Model"),
    ("/provider", "scope the model picker to one provider", "Model"),
    ("/thinking", "set the reasoning effort level", "Model"),
    ("/mode", "switch permission mode: ask or auto", "Security"),
    ("/permissions", "view permission mode, rules, and session grants", "Security"),
    ("/login", "sign in to a provider", "Account"),
    ("/logout", "clear a provider's credentials", "Account"),
    ("/settings", "toggle behaviour and appearance", "Appearance"),
    ("/themes", "switch palette", "Appearance"),
    ("/notifications", "turn notification sounds on or off", "Appearance"),
    ("/new", "start a fresh session", "Session"),
    ("/clear", "clear the transcript, keep the session", "Session"),
    ("/tree", "jump to an earlier point in the conversation", "Session"),
    ("/attach", "attach an image to the next prompt", "Session"),
    ("/compact", "summarise the context window", "Session"),
    ("/undo", "rewind the last turn and its files", "Session"),
    ("/redo", "step forward again", "Session"),
    ("/session", "switch to another session", "Session"),
    ("/usage", "token usage for this session and overall", "Session"),
    ("/export", "write the session to an HTML file", "Session"),
    ("/copy", "copy the last answer to the clipboard", "Session"),
    ("/handoff", "carry this work into a new session", "Session"),
    ("/recap", "summarise what has happened so far", "Session"),
    ("/agent", "switch or reload agents", "Agents"),
    ("/skills", "list the loaded skills", "Workspace"),
    ("/tools", "list the available tools", "Workspace"),
    ("/mcp", "manage MCP servers", "Extensions"),
    ("/reload", "reload config, extensions and tools", "Extensions"),
    ("/update", "update ctx to the latest release", "Extensions"),
    ("/quit", "exit vx", "General"),
]

#: Aliases accepted on input, mapped to the command they run.
#: Keys worth knowing, listed under the commands in ``/help``; the composer's
#: readline set is not discoverable from the screen otherwise.
KEY_BINDINGS: list[tuple[str, str]] = [
    ("ctrl+g", "fold the sidebar's sections"),
    ("ctrl+f", "search the transcript"),
    ("n / N", "next / previous search match"),
    ("ctrl+end", "jump to the latest message"),
    ("ctrl+r", "search prompt history"),
    ("ctrl+v", "attach a clipboard image, or paste text"),
    ("ctrl+x", "drop the attached images"),
    ("ctrl+z / ctrl+y", "undo / redo the composer"),
    ("alt+b / alt+f", "move by word"),
    ("ctrl+left / right", "move by word"),
    ("ctrl+y", "yank the last kill"),
    ("alt+y", "cycle the kill ring"),
    ("shift+enter", "new line without sending"),
    ("ctrl+o", "expand !cmd output"),
    ("pgup / pgdn", "scroll the transcript, or the composer when it fills"),
    ("wheel", "scroll the transcript"),
    ("shift+drag", "select text"),
    ("esc", "interrupt the turn, or clear the composer"),
    ("ctrl+c twice", "exit"),
    ("tab / shift+tab", "cycle reasoning effort / permission mode"),
]

SLASH_ALIASES: dict[str, str] = {
    "exit": "quit",
    "q": "quit",
    "effort": "thinking",
    "think": "thinking",
    "perm": "permissions",
    "switch": "agent",
    "theme": "themes",
    "notification": "notifications",
    "ls": "tools",
    "commands": "help",
}

#: Commands that need the runtime to be up before they can do anything.
NEEDS_RUNTIME = frozenset(
    {
        "model",
        "provider",
        "thinking",
        "mode",
        "permissions",
        "login",
        "logout",
        "settings",
        "themes",
        "notifications",
        "new",
        "clear",
        "tree",
        "compact",
        "undo",
        "redo",
        "session",
        "usage",
        "export",
        "copy",
        "handoff",
        "recap",
        "agent",
        "skills",
        "tools",
        "mcp",
        "reload",
    }
)


class CommandsMixin(
    SettingsCommands,
    ModelCommands,
    SessionCommands,
    AgentCommands,
    AuthCommands,
    SystemCommands,
    CommandSupport,
):
    """``/command`` handling for the vx app."""

    def handle_slash(self, text: str) -> bool:
        """Run one slash command. Returns ``True`` when it was handled."""
        body = text[1:] if text.startswith("/") else text
        parts = body.split(None, 1)
        raw = parts[0].lower() if parts else ""
        args = parts[1].strip() if len(parts) > 1 else ""
        cmd = SLASH_ALIASES.get(raw, raw)

        if cmd in ("quit", "exit", "q"):
            self._exiting = True
            return True
        if cmd == "help":
            self._show_help_menu()
            return True
        if not self._runtime_ready(cmd):
            return True

        handlers: dict[str, Any] = {
            "model": lambda: self._handle_model_command(args),
            "provider": lambda: self._handle_provider_command(args),
            "thinking": lambda: self._handle_thinking_command(args),
            "mode": lambda: self._handle_mode_command(args),
            "permissions": lambda: self._handle_permissions_command(args),
            "login": lambda: self._handle_login_command(args),
            "logout": lambda: self._handle_logout_command(args),
            "settings": lambda: self._handle_settings_command(args),
            "themes": lambda: self._handle_themes_command(args),
            "notifications": lambda: self._handle_notifications_command(args),
            "new": self._handle_new_command,
            "clear": self._handle_clear_command,
            "tree": self._handle_tree_command,
            "attach": lambda: self._handle_attach_command(args),
            "compact": lambda: self._handle_compact_command(args),
            "undo": self._handle_undo_command,
            "redo": self._handle_redo_command,
            "session": self._handle_session_command,
            "usage": self._handle_usage_command,
            "export": self._handle_export_command,
            "copy": self._handle_copy_command,
            "handoff": lambda: self._handle_handoff_command(args),
            "recap": self._handle_recap_command,
            "agent": lambda: self._handle_agent_command(args),
            "skills": self._handle_skills_command,
            "tools": self._handle_tools_command,
            "mcp": lambda: self._handle_mcp_command(args),
            "reload": lambda: self._handle_reload_command(args),
            "update": self._handle_update_command,
            "find": lambda: self._handle_find_command(args),
        }
        handler = handlers.get(cmd)
        if handler is None:
            extension = self._extension_command(cmd, args)
            if extension:
                return True
            if self._skill_command(cmd, args):
                return True
            self._warn(f"unknown command: /{cmd}")
            return True
        try:
            handler()
        except Exception as exc:  # a broken command must not kill the app
            self._fail(f"/{cmd} failed: {exc}")
        return True

    def _runtime_ready(self, cmd: str) -> bool:
        if cmd not in NEEDS_RUNTIME or self.runtime is not None:
            return True
        self._warn(f"/{cmd}: still starting — the runtime is not ready yet")
        return False

    def _skill_command(self, name: str, args: str) -> bool:
        """A skill with ``register_cmd: true`` is reachable as ``/<skill>``."""
        skill = next(
            (s for s in self._context_skills() if s.register_cmd and s.name == name), None
        )
        if skill is None:
            return False
        from ctx.agent.skills import render_skill_prompt

        try:
            prompt = render_skill_prompt(skill, args)
        except Exception as exc:
            self._fail(f"skill /{name} failed: {exc}")
            return True
        self.viewport.add(blocks.UserTurn(text=f"/{name} {args}".strip()))
        self._enqueue_or_run(prompt)
        return True

    def _context_skills(self) -> list[Any]:
        rt = self.runtime
        context = getattr(rt, "context", None) if rt is not None else None
        return list(getattr(context, "skills", None) or [])

    def _extension_command(self, name: str, args: str) -> bool:
        """Let extensions claim commands the built-ins did not handle."""
        loaded = getattr(self, "_loaded", None)
        commands = getattr(loaded, "all_commands", None) or {}
        command = commands.get(name)
        if command is None:
            return False
        try:
            outcome = command.handler(args)
        except Exception as exc:
            self._fail(f"extension command /{command.name} failed: {exc}")
            return True
        output = getattr(outcome, "output", "")
        if output:
            self._notice(f"/{command.name} (from {command.owner}): {output}")
        return True

    def _show_help_menu(self) -> None:
        items = [
            footer.MenuItem(label=name, detail=description, metadata=category, value=name)
            for name, description, category in SLASH_COMMANDS
        ]
        items.extend(
            footer.MenuItem(label=keys, detail=description, metadata="Keys", value="key:")
            for keys, description in KEY_BINDINGS
        )
        self._open_menu(
            items,
            noun="Commands",
            on_select=lambda item: self._run_help_choice(item.value or item.label),
            filterable=True,
        )

    def _run_help_choice(self, name: str) -> None:
        """A command fills the composer; a key row is reference only."""
        if name.startswith("key:"):
            return
        self._input = "/" + name.lstrip("/") + " "
        self._cursor = len(self._input)

    def _handle_find_command(self, args: str) -> None:
        """``/find query`` opens the same search UI as ctrl+f, prefilled."""
        self._searching = True
        self._set_search(args)
        if self.viewport.search_matches:
            self.viewport.search_next(0, *self._size())


__all__ = ["NEEDS_RUNTIME", "SLASH_ALIASES", "SLASH_COMMANDS", "CommandsMixin"]
