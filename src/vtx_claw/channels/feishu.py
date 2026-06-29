from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from aiohttp import web

from vtx_claw.channels.base import ChannelPlugin, InboundMessage

logger = logging.getLogger(__name__)


class FeishuAdapter(ChannelPlugin):
    id = "feishu"
    label = "Feishu (Lark)"

    def __init__(self) -> None:
        super().__init__()
        self._app_id = ""
        self._app_secret = ""
        self._verify_token = ""
        self._encrypt_key = ""
        self._tenant_token = ""
        self._token_expires = 0.0
        self._ws_client = None

    async def on_start(self, config: dict[str, Any]) -> None:
        self._app_id = config.get("app_id", "")
        self._app_secret = config.get("app_secret", "")
        self._verify_token = config.get("verify_token", "")
        self._encrypt_key = config.get("encrypt_key", "")

        if not self._app_id or not self._app_secret:
            raise ValueError("Feishu app_id and app_secret are required")

        use_ws = config.get("use_websocket", True)
        if use_ws:
            await self._start_websocket()
        else:
            await self._start_webhook(config)

        logger.info("Feishu started (websocket=%s)", use_ws)

    async def _start_websocket(self) -> None:
        try:
            import websockets

            ws_url = "wss://open.feishu.cn/event/ws"
            self._ws_client = await websockets.connect(ws_url)
            logger.info("Feishu WebSocket connected")
            self._ws_task = asyncio.create_task(self._ws_listen())
        except ImportError:
            logger.warning("websockets not installed, falling back to webhook mode")
        except Exception:
            logger.exception("Feishu WebSocket connection failed")

    async def _ws_listen(self) -> None:
        if not self._ws_client:
            return
        try:
            async for raw in self._ws_client:
                data = json.loads(raw)
                await self._handle_event(data)
        except Exception:
            logger.exception("Feishu WebSocket error")

    async def _start_webhook(self, config: dict[str, Any]) -> None:
        port = config.get("webhook_port", 19001)
        app = web.Application()
        app.router.add_post("/feishu/webhook", self._handle_webhook)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info("Feishu webhook on port %d", port)

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        body = await request.json()

        if body.get("type") == "url_verification":
            return web.json_response({"challenge": body.get("challenge", "")})

        await self._handle_event(body)
        return web.json_response({"code": 0})

    async def _handle_event(self, data: dict[str, Any]) -> None:
        header = data.get("header", {})
        event = data.get("event", {})

        if header.get("event_type") == "im.message.receive_v1":
            message = event.get("message", {})
            sender = event.get("sender", {}).get("sender_id", {})

            chat_id = message.get("chat_id", "")
            content = json.loads(message.get("content", "{}"))
            text = content.get("text", "")

            if not text:
                return

            inbound = InboundMessage(
                channel="feishu",
                message_id=message.get("message_id", ""),
                sender_id=sender.get("open_id", ""),
                sender_name=sender.get("open_id", ""),
                chat_id=chat_id,
                chat_type=message.get("chat_type", "direct"),
                text=text,
                timestamp=str(int(time.time())),
                account_id=self._app_id,
            )
            await self._handle_message(inbound)

    async def _get_tenant_token(self) -> str:
        import httpx

        if self._tenant_token and time.time() < self._token_expires:
            return self._tenant_token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self._app_id, "app_secret": self._app_secret},
            )
            data = resp.json()
            self._tenant_token = data.get("tenant_access_token", "")
            self._token_expires = time.time() + data.get("expire", 7200) - 300
            return self._tenant_token

    async def on_stop(self) -> None:
        if self._ws_client:
            await self._ws_client.close()
        logger.info("Feishu stopped")

    async def send_text(self, target: str, text: str, reply_to: str | None = None) -> str:
        import httpx

        token = await self._get_tenant_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {"receive_id": target, "msg_type": "text", "content": json.dumps({"text": text})}
        if reply_to:
            payload["reply_in_thread"] = False

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                headers=headers,
                json=payload,
            )
            data = resp.json()
            return data.get("data", {}).get("message_id", "")

    async def send_card(self, target: str, card: dict[str, Any]) -> str:
        import httpx

        token = await self._get_tenant_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {"receive_id": target, "msg_type": "interactive", "content": json.dumps(card)}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                headers=headers,
                json=payload,
            )
            data = resp.json()
            return data.get("data", {}).get("message_id", "")
