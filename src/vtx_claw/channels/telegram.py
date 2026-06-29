from __future__ import annotations

import logging
from typing import Any

from vtx_claw.channels.base import ChannelPlugin, InboundMessage

logger = logging.getLogger(__name__)


class TelegramAdapter(ChannelPlugin):
    id = "telegram"
    label = "Telegram"

    def __init__(self) -> None:
        super().__init__()
        self._app = None
        self._bot = None

    async def on_start(self, config: dict[str, Any]) -> None:
        bot_token = config.get("bot_token", "")
        if not bot_token:
            raise ValueError("Telegram bot_token is required")

        try:
            from telegram import Update
            from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

            builder = ApplicationBuilder().token(bot_token)
            self._app = builder.build()

            async def cmd_start(update: Update, context: Any) -> None:
                if update.message:
                    await update.message.reply_text(
                        "Hi! I'm your VTX Claw assistant. Send me a message!"
                    )

            async def cmd_stop(update: Update, context: Any) -> None:
                if update.message:
                    await update.message.reply_text("Session ended. Send /start to begin again.")

            async def cmd_queue(update: Update, context: Any) -> None:
                if update.message:
                    await update.message.reply_text("Queue control: /stop to abort current run.")

            async def handle_message(update: Update, context: Any) -> None:
                msg = update.message
                if msg is None or msg.text is None:
                    return
                user = msg.from_user
                inbound = InboundMessage(
                    channel="telegram",
                    message_id=str(msg.message_id),
                    sender_id=str(user.id) if user else "",
                    sender_name=user.first_name if user else "",
                    chat_id=str(msg.chat_id),
                    chat_type=msg.chat.type or "direct",
                    text=msg.text,
                    timestamp=msg.date.isoformat() if msg.date else "",
                    reply_to=str(msg.reply_to_message.message_id) if msg.reply_to_message else "",
                    account_id=config.get("account_id", ""),
                )
                await self._handle_message(inbound)

            self._app.add_handler(CommandHandler("start", cmd_start))
            self._app.add_handler(CommandHandler("stop", cmd_stop))
            self._app.add_handler(CommandHandler("queue", cmd_queue))
            self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

            await self._app.initialize()
            self._bot = self._app.bot
            await self._app.start()
            if self._app.updater is not None:
                await self._app.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram bot started (polling)")
        except ImportError:
            logger.error("python-telegram-bot not installed")
            raise

    async def on_stop(self) -> None:
        if self._app:
            try:
                if self._app.updater is not None:
                    await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception:
                logger.exception("Error stopping Telegram")
        logger.info("Telegram stopped")

    async def send_text(self, target: str, text: str, reply_to: str | None = None) -> str:
        if not self._bot:
            raise RuntimeError("Telegram bot not initialized")

        chunk_limit = 4096
        sent_ids = []
        for i in range(0, len(text), chunk_limit):
            chunk = text[i : i + chunk_limit]
            kwargs: dict[str, Any] = {"chat_id": target, "text": chunk}
            if reply_to and i == 0:
                kwargs["reply_to_message_id"] = int(reply_to)
            msg = await self._bot.send_message(**kwargs)
            sent_ids.append(str(msg.message_id))
        return sent_ids[-1] if sent_ids else ""
