from __future__ import annotations

import logging

from vtx_claw.channels.base import ChannelPlugin

logger = logging.getLogger(__name__)


class WebChannel(ChannelPlugin):
    id = "web"
    label = "Web UI"

    async def send_text(self, target: str, text: str, reply_to: str | None = None) -> str:
        logger.debug("Web send to %s: %s", target, text[:80])
        return ""
