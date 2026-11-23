"""``/update`` slash command — check for and install latest ctx update."""

from __future__ import annotations

import asyncio

from ctx.code.self_update import self_update
from ctx.term.chat import ChatLog
from ctx.term.commands.base import CommandSupport


class UpdateCommands(CommandSupport):
    """``/update`` command handling."""

    def _handle_update_command(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        chat.add_info_message("Checking for updates and updating ctx...")
        self.run_worker(self._do_update(), exclusive=False)

    async def _do_update(self) -> None:
        ok, msg = await asyncio.to_thread(self_update)
        chat = self.query_one("#chat-log", ChatLog)
        if ok:
            if "already up to date" in msg.lower():
                chat.add_info_message(msg)
            else:
                chat.add_info_message(f"{msg} Please restart ctx to apply changes.")
        else:
            chat.add_info_message(f"Update failed: {msg}", error=True)
