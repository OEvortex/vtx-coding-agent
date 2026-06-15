"""Slash-command handling for the Vtx app, split by domain:

- settings.py - /settings, /themes, /permissions, /thinking, /notifications
- models.py   - /model
- sessions.py - /clear, /new, /resume, /tree, /session, /handoff, /compact, /export, /copy
- auth.py     - /login, /logout

CommandsMixin composes the domain mixins and owns the command router.
"""

from __future__ import annotations

from ..chat import ChatLog
from .auth import AuthCommands
from .base import CommandSupport
from .models import ModelCommands
from .sessions import SessionCommands
from .settings import SettingsCommands, SettingsSelectionResult


class CommandsMixin(SettingsCommands, ModelCommands, SessionCommands, AuthCommands):
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

        return False

    def _show_help(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        chat.add_help_details()


__all__ = [
    "AuthCommands",
    "CommandSupport",
    "CommandsMixin",
    "ModelCommands",
    "SessionCommands",
    "SettingsCommands",
    "SettingsSelectionResult",
]
