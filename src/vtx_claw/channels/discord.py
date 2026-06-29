from __future__ import annotations

import logging
from typing import Any

from vtx_claw.channels.base import ChannelPlugin, InboundMessage

logger = logging.getLogger(__name__)


class DiscordAdapter(ChannelPlugin):
    id = "discord"
    label = "Discord"

    def __init__(self) -> None:
        super().__init__()
        self._client = None

    async def on_start(self, config: dict[str, Any]) -> None:
        bot_token = config.get("bot_token", "")
        if not bot_token:
            raise ValueError("Discord bot_token is required")

        try:
            import discord
            from discord.ext import commands

            intents = discord.Intents.default()
            intents.message_content = True
            intents.dm_messages = True

            client = commands.Bot(command_prefix="!", intents=intents)
            self._client = client

            @client.event
            async def on_ready() -> None:
                logger.info("Discord bot logged in as %s", client.user)

            @client.event
            async def on_message(message: discord.Message) -> None:
                if message.author == client.user:
                    return
                if not message.content:
                    return

                inbound = InboundMessage(
                    channel="discord",
                    message_id=str(message.id),
                    sender_id=str(message.author.id),
                    sender_name=message.author.display_name,
                    chat_id=str(message.channel.id),
                    chat_type="group" if hasattr(message.channel, "guilds") else "direct",
                    text=message.content,
                    timestamp=message.created_at.isoformat(),
                    reply_to=str(message.reference.message_id) if message.reference else "",
                    account_id=config.get("account_id", ""),
                )
                await self._handle_message(inbound)

            self._client.loop.create_task(self._client.start(bot_token))
            logger.info("Discord bot starting")
        except ImportError:
            logger.error("discord.py not installed")
            raise

    async def on_stop(self) -> None:
        if self._client:
            await self._client.close()
        logger.info("Discord stopped")

    async def send_text(self, target: str, text: str, reply_to: str | None = None) -> str:
        if not self._client:
            raise RuntimeError("Discord client not initialized")

        try:
            channel = self._client.get_channel(int(target))
            if not channel:
                channel = await self._client.fetch_channel(int(target))

            from typing import Any, cast

            msg = await cast(Any, channel).send(text)
            return str(msg.id)
        except Exception:
            logger.exception("Discord send failed")
            return ""
