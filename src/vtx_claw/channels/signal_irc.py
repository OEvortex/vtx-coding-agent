from __future__ import annotations

import asyncio
import logging
from typing import Any

from vtx_claw.channels.base import ChannelPlugin

logger = logging.getLogger(__name__)


class IRCAdapter(ChannelPlugin):
    id = "irc"
    label = "IRC"
    capabilities = ChannelPlugin.capabilities  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._server = ""
        self._nick = "vtx-bot"

    async def on_start(self, config: dict[str, Any]) -> None:
        self._server = config.get("server", "")
        self._nick = config.get("nick", "vtx-bot")
        logger.info("[irc] configured server=%s nick=%s", self._server, self._nick)

    async def send_text(self, target: str, text: str, reply_to: str | None = None) -> str:
        return target


class IRCConfig:
    def __init__(self) -> None:
        self.enabled = False
        self.server = ""
        self.nick = "vtx-bot"
        self.channels: list[str] = []
