"""``/update`` slash command — check for and install latest vtx update."""

from __future__ import annotations

import asyncio

from vtx.coding_agent.self_update import self_update
from vtx.tui.chat import ChatLog
from vtx.tui.commands.base import CommandSupport


class UpdateCommands(CommandSupport):
    """``/update`` command handling."""

    def _handle_update_command(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        chat.add_info_message("Checking for updates and updating vtx...")
        self.run_worker(self._do_update(), exclusive=False)

    async def _do_update(self) -> None:
        ok, msg = await asyncio.to_thread(self_update)
        chat = self.query_one("#chat-log", ChatLog)
        if ok:
            if "already up to date" in msg.lower():
                chat.add_info_message(msg)
            else:
                chat.add_info_message(f"{msg} Please restart vtx to apply changes.")
        else:
            chat.add_info_message(f"Update failed: {msg}", error=True)
