from __future__ import annotations

import logging
from typing import Any

from vtx_claw.channels.base import ChannelPlugin

logger = logging.getLogger(__name__)


class SlackAdapter(ChannelPlugin):
    id = "slack"
    label = "Slack"
    capabilities = ChannelPlugin.capabilities  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._bot_token = ""
        self._signing_secret = ""

    async def on_start(self, config: dict[str, Any]) -> None:
        self._bot_token = config.get("bot_token", "")
        self._signing_secret = config.get("signing_secret", "")
        logger.info(
            "[slack] configured (token=%s...)", self._bot_token[:4] if self._bot_token else ""
        )

    async def send_text(self, target: str, text: str, reply_to: str | None = None) -> str:
        return target
