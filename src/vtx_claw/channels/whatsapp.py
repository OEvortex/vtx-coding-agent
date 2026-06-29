from __future__ import annotations

import logging
import time
from typing import Any

from aiohttp import web

from vtx_claw.channels.base import ChannelPlugin, InboundMessage

logger = logging.getLogger(__name__)


class WhatsAppAdapter(ChannelPlugin):
    id = "whatsapp"
    label = "WhatsApp"

    def __init__(self) -> None:
        super().__init__()
        self._token = ""
        self._phone_number_id = ""
        self._verify_token = ""

    async def on_start(self, config: dict[str, Any]) -> None:
        self._token = config.get("token", "")
        self._phone_number_id = config.get("phone_number_id", "")
        self._verify_token = config.get("verify_token", "vtx-claw-verify")

        if not self._token:
            raise ValueError("WhatsApp token is required")

        port = config.get("webhook_port", 19002)
        app = web.Application()
        app.router.add_get("/whatsapp/webhook", self._verify_webhook)
        app.router.add_post("/whatsapp/webhook", self._handle_webhook)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info("WhatsApp webhook on port %d", port)

    async def _verify_webhook(self, request: web.Request) -> web.Response:
        mode = request.query.get("hub.mode")
        token = request.query.get("hub.verify_token")
        challenge = request.query.get("hub.challenge")

        if mode == "subscribe" and token == self._verify_token:
            return web.Response(text=challenge or "")
        return web.Response(status=403)

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        body = await request.json()
        entries = body.get("entry", [])

        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                for msg in messages:
                    text = msg.get("text", {}).get("body", "")
                    if not text:
                        continue

                    inbound = InboundMessage(
                        channel="whatsapp",
                        message_id=msg.get("id", ""),
                        sender_id=msg.get("from", ""),
                        sender_name=msg.get("from", ""),
                        chat_id=msg.get("from", ""),
                        chat_type="direct",
                        text=text,
                        timestamp=msg.get("timestamp", str(int(time.time()))),
                        account_id=self._phone_number_id,
                    )
                    await self._handle_message(inbound)

        return web.json_response({"status": "ok"})

    async def on_stop(self) -> None:
        logger.info("WhatsApp stopped")

    async def send_text(self, target: str, text: str, reply_to: str | None = None) -> str:
        import httpx

        url = f"https://graph.facebook.com/v18.0/{self._phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        payload = {
            "messaging_product": "whatsapp",
            "to": target,
            "type": "text",
            "text": {"body": text},
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=payload)
            data = resp.json()
            msgs = data.get("messages", [])
            return msgs[0].get("id", "") if msgs else ""
