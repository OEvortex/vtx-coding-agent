"""Slash-command dispatch for vx.

The verb set, arguments and user-facing wording are ported from
``packages/code/src/ctx/term/commands`` so the two front ends behave the same.
What differs is the surface: vx answers with a transcript block, a footer
picker, or the composer used as a one-line prompt.

- settings.py — /settings, /themes, /permissions, /thinking, /effort,
  /notifications, /ponytail
- models.py   — /model, /provider
- sessions.py — /new, /clear, /resume, /tree, /session, /handoff, /compact,
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
from vx.tui import footer

#: name, description, presentation category. fx groups commands this way and
#: vx shows the category in the picker's metadata column.
SLASH_COMMANDS: list[tuple[str, str, str]] = [
    ("/help", "show commands and key bindings", "General"),
    ("/model", "choose the model, or refresh the catalog", "Model"),
    ("/provider", "scope the model picker to one provider", "Model"),
    ("/thinking", "set the reasoning effort level", "Model"),
    ("/permissions", "ask before tools run, or allow them", "Security"),
    ("/login", "sign in to a provider", "Account"),
    ("/logout", "clear a provider's credentials", "Account"),
    ("/settings", "toggle behaviour and appearance", "Appearance"),
    ("/themes", "switch palette", "Appearance"),
    ("/notifications", "turn notification sounds on or off", "Appearance"),
    ("/ponytail", "toggle the terse prompt", "Appearance"),
    ("/new", "start a fresh session", "Session"),
    ("/clear", "clear the transcript, keep the session", "Session"),
    ("/resume", "open a saved session", "Session"),
    ("/tree", "jump to an earlier point in the conversation", "Session"),
    ("/compact", "summarise the context window", "Session"),
    ("/undo", "rewind the last turn and its files", "Session"),
    ("/redo", "step forward again", "Session"),
    ("/session", "show this session's details", "Session"),
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
        "permissions",
        "login",
        "logout",
        "settings",
        "themes",
        "notifications",
        "ponytail",
        "new",
        "clear",
        "resume",
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
            "permissions": lambda: self._handle_permissions_command(args),
            "login": lambda: self._handle_login_command(args),
            "logout": lambda: self._handle_logout_command(args),
            "settings": lambda: self._handle_settings_command(args),
            "themes": lambda: self._handle_themes_command(args),
            "notifications": lambda: self._handle_notifications_command(args),
            "ponytail": lambda: self._handle_ponytail_command(args),
            "new": self._handle_new_command,
            "clear": self._handle_clear_command,
            "resume": self._handle_resume_command,
            "tree": self._handle_tree_command,
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
        }
        handler = handlers.get(cmd)
        if handler is None:
            extension = self._extension_command(cmd, args)
            if extension:
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
        self._open_menu(
            [
                footer.MenuItem(label=name, detail=description, metadata=category, value=name)
                for name, description, category in SLASH_COMMANDS
            ],
            noun="Commands",
            on_select=lambda item: self._run_help_choice(item.value or item.label),
            filterable=True,
        )

    def _run_help_choice(self, name: str) -> None:
        self._input = name
        self._cursor = len(self._input)


__all__ = ["NEEDS_RUNTIME", "SLASH_ALIASES", "SLASH_COMMANDS", "CommandsMixin"]
