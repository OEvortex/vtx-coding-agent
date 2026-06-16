"""Slash-command handling for the Vtx app, split by domain:

- settings.py - /settings, /themes, /permissions, /thinking, /notifications
- models.py   - /model
- sessions.py - /clear, /new, /resume, /tree, /session, /handoff, /compact, /export, /copy
- auth.py     - /login, /logout
- providers.py - /provider

CommandsMixin composes the domain mixins and owns the command router.
"""

from __future__ import annotations

from ..chat import ChatLog
from .auth import AuthCommands
from .base import CommandSupport
from .models import ModelCommands
from .providers import ProviderCommands
from .sessions import SessionCommands
from .settings import SettingsCommands, SettingsSelectionResult


class CommandsMixin(
    SettingsCommands, ModelCommands, SessionCommands, AuthCommands, ProviderCommands
):
    def _handle_command(self, text: str) -> bool:
        parts = text[1:].split(maxsplit=1)
        cmd = parts[0] if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        if cmd in ("quit", "exit", "q"):
            self.exit()
            return True
        if cmd == "help":
            self._show_help()
            return True
        if cmd == "clear":
            self._clear_conversation()
            return True
        if cmd == "model":
            self._handle_model_command(args)
            return True
        if cmd == "provider":
            self._handle_provider_command(args)
            return True
        if cmd == "new":
            self._new_conversation()
            return True
        if cmd == "settings":
            self._handle_settings_command()
            return True
        if cmd == "themes":
            self._handle_themes_command(args)
            return True
        if cmd == "permissions":
            self._handle_permissions_command(args)
            return True
        if cmd == "thinking":
            self._handle_thinking_command(args)
            return True
        if cmd == "notifications":
            self._handle_notifications_command(args)
            return True
        if cmd == "handoff":
            self._handle_handoff_command(args)
            return True
        if cmd == "resume":
            self._show_resume_sessions()
            return True
        if cmd == "tree":
            self._show_tree_selector()
            return True
        if cmd == "session":
            self._show_session_info()
            return True
        if cmd == "login":
            self._handle_login_command(args)
            return True
        if cmd == "logout":
            self._handle_logout_command(args)
            return True
        if cmd == "export":
            self._handle_export_command()
            return True
        if cmd == "copy":
            self._handle_copy_command()
            return True
        if cmd == "compact":
            self._handle_compact_command()
            return True

        # Extension commands take a final swing at anything the built-ins
        # did not handle. They can shadow built-in commands; this matches
        # pi's behavior of letting extensions override the agent's UI.
        ext_cmd = self._extension_command_lookup(cmd)
        if ext_cmd is not None:
            self._dispatch_extension_command(ext_cmd, args)
            return True

        return False

    def _extension_command_lookup(self, name: str):
        """Return the registered extension command for ``name`` or ``None``."""
        # Late import keeps commands/__init__.py importable without the
        # extension bus being available (e.g. from tests that never load
        # extensions).
        loaded = getattr(self, "_loaded_extensions", None)
        if loaded is None:
            return None
        return loaded.all_commands.get(name)

    def _dispatch_extension_command(self, cmd, args: str) -> None:
        try:
            outcome = cmd.handler(args)
        except Exception as exc:  # never crash on a buggy extension
            chat = self.query_one("#chat-log", ChatLog)
            chat.add_info_message(f"Extension command /{cmd.name} failed: {exc}", error=True)
            return

        if not outcome.output:
            return
        chat = self.query_one("#chat-log", ChatLog)
        chat.add_info_message(f"/{cmd.name} (from {cmd.owner}): {outcome.output}")

    def _show_help(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        chat.add_help_details()


__all__ = [
    "AuthCommands",
    "CommandSupport",
    "CommandsMixin",
    "ModelCommands",
    "ProviderCommands",
    "SessionCommands",
    "SettingsCommands",
    "SettingsSelectionResult",
]
