from __future__ import annotations

import logging
from typing import Any

from vtx_claw.channels.base import ChannelPlugin, MessageHandler

logger = logging.getLogger(__name__)


class ChannelManager:
    def __init__(self) -> None:
        self._channels: dict[str, ChannelPlugin] = {}
        self._configs: dict[str, dict[str, Any]] = {}
        self._message_handler: MessageHandler | None = None
        self._event_listeners: list = []

    def register(self, channel_id: str, plugin: ChannelPlugin) -> None:
        self._channels[channel_id] = plugin
        logger.info("Channel registered: %s", channel_id)

    def configure(self, channel_id: str, config: dict[str, Any]) -> None:
        self._configs[channel_id] = config

    def set_message_handler(self, handler: MessageHandler) -> None:
        self._message_handler = handler
        for plugin in self._channels.values():
            plugin.set_message_handler(handler)

    def add_event_listener(self, listener: Any) -> None:
        self._event_listeners.append(listener)

    async def start_all(self) -> dict[str, bool]:
        results = {}
        for channel_id, plugin in self._channels.items():
            config = self._configs.get(channel_id, {})
            if not config.get("enabled", False):
                results[channel_id] = False
                continue
            try:
                if self._message_handler:
                    plugin.set_message_handler(self._message_handler)
                await plugin.start(config)
                results[channel_id] = True
            except Exception:
                logger.exception("Failed to start channel %s", channel_id)
                results[channel_id] = False
        return results

    async def stop_all(self) -> dict[str, bool]:
        results = {}
        for channel_id, plugin in self._channels.items():
            try:
                await plugin.stop()
                results[channel_id] = True
            except Exception:
                logger.exception("Failed to stop channel %s", channel_id)
                results[channel_id] = False
        return results

    def get(self, channel_id: str) -> ChannelPlugin | None:
        return self._channels.get(channel_id)

    def list_running(self) -> list[str]:
        return [cid for cid, p in self._channels.items() if p.is_running()]

    def list_all(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self._channels.values()]

    async def send_message(
        self, channel_id: str, target: str, text: str, reply_to: str = ""
    ) -> str:
        plugin = self._channels.get(channel_id)
        if not plugin:
            raise ValueError(f"Channel {channel_id} not found")
        return await plugin.send_text(target, text, reply_to=reply_to or None)
