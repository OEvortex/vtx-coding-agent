from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ChannelCapabilities:
    chat_types: list[str] = field(default_factory=lambda: ["direct", "group"])
    supports_media: bool = False
    supports_reactions: bool = False
    supports_threads: bool = False
    supports_edit: bool = False
    supports_reply: bool = True
    block_streaming: bool = False
    text_chunk_limit: int | None = None


@dataclass
class InboundMessage:
    channel: str
    message_id: str
    sender_id: str
    sender_name: str
    chat_id: str
    chat_type: str  # direct, group, channel
    text: str
    timestamp: str
    reply_to: str = ""
    account_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class OutboundMessage:
    channel: str
    target: str
    text: str
    reply_to: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


MessageHandler = Callable[[InboundMessage], Awaitable[None]]


class ChannelPlugin(ABC):
    id: str = ""
    label: str = ""
    capabilities: ChannelCapabilities = field(default_factory=ChannelCapabilities)

    def __init__(self) -> None:
        self._message_handler: MessageHandler | None = None
        self._running = False

    async def start(self, config: dict[str, Any]) -> None:
        if self._running:
            return
        try:
            await self.on_init()
            await self.on_start(config)
            self._running = True
            await self.on_ready()
            logger.info("[%s] Channel started", self.id)
        except Exception:
            logger.exception("[%s] Channel start failed", self.id)
            raise

    async def stop(self) -> None:
        try:
            await self.on_stop()
            self._running = False
            await self.on_destroy()
            logger.info("[%s] Channel stopped", self.id)
        except Exception:
            logger.exception("[%s] Channel stop failed", self.id)
            raise

    async def on_init(self) -> None:
        """Called before start(). Override for initialization."""
        return

    async def on_start(self, config: dict[str, Any]) -> None:
        """Called during start(). Override to connect to platform."""
        return

    async def on_ready(self) -> None:
        """Called after start() completes. Override for post-connection setup."""
        return

    async def on_stop(self) -> None:
        """Called during stop(). Override to disconnect from platform."""
        return

    async def on_destroy(self) -> None:
        """Called after stop(). Override for final cleanup."""
        return

    async def on_error(self, error: Exception) -> None:
        logger.error("[%s] Error: %s", self.id, error, exc_info=True)

    @abstractmethod
    async def send_text(self, target: str, text: str, reply_to: str | None = None) -> str: ...

    async def send_media(
        self,
        target: str,
        media_url: str,
        media_type: str,
        caption: str | None = None,
        reply_to: str | None = None,
    ) -> str:
        raise NotImplementedError("Media not supported")

    def set_message_handler(self, handler: MessageHandler) -> None:
        self._message_handler = handler

    async def _handle_message(self, message: InboundMessage) -> None:
        if self._message_handler:
            try:
                await self._message_handler(message)
            except Exception:
                logger.exception("[%s] Message handler error", self.id)

    def is_running(self) -> bool:
        return self._running

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "running": self._running,
            "capabilities": {
                "chat_types": self.capabilities.chat_types,
                "supports_media": self.capabilities.supports_media,
            },
        }
