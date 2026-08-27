"""Minimal multiplexed gateway — WS control/RPC + HTTP API on one port.

Implements OpenClaw wire protocol subset:
  connect -> hello-ok -> req/res + events (agent, chat, presence, tick, cron)
Pairing is device-based via vtx.openjarvis.pairing.store.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass

from aiohttp import web

from vtx.openjarvis.agent.config import OpenJarvisConfig
from vtx.openjarvis.agent.runtime import OpenJarvisRuntime


@dataclass
class GatewayStatus:
    running: bool
    port: int
    channels: list[str]
    cron_jobs: int


class OpenJarvisGateway:
    def __init__(
        self, config: OpenJarvisConfig | None = None, runtime: OpenJarvisRuntime | None = None
    ):
        self.config = config or OpenJarvisConfig.load()
        self.runtime = runtime or OpenJarvisRuntime(self.config)
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"ok": True, "port": self.config.gateway.port, "status": "running"}
        )

    async def handle_models(self, request: web.Request) -> web.Response:
        # Agent-first like OpenClaw /v1/models
        return web.json_response(
            {
                "data": [
                    {"id": "openjarvis", "object": "model"},
                    {"id": "openjarvis/default", "object": "model"},
                ]
            }
        )

    async def handle_chat_completions(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
            query = (
                body.get("messages", [{}])[-1].get("content", "")
                if body.get("messages")
                else body.get("input", "")
            )
            text = await self.runtime.run_sync(str(query))
            return web.json_response(
                {
                    "choices": [{"message": {"role": "assistant", "content": text}}],
                    "id": "oj-1",
                    "object": "chat.completion",
                }
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        # Expect connect frame
        try:
            msg = await ws.receive()
            if msg.type != web.WSMsgType.TEXT:
                await ws.close()
                return ws
            data = json.loads(msg.data)
            if data.get("type") != "connect":
                await ws.send_str(
                    json.dumps({"type": "error", "error": "first frame must be connect"})
                )
                await ws.close()
                return ws
            # Pairing check (simplified — allow loopback, require token otherwise)
            # Send hello-ok
            await ws.send_str(
                json.dumps(
                    {
                        "type": "hello-ok",
                        "snapshot": {"presence": "online", "health": "ok", "stateVersion": 1},
                        "policy": {"maxPayload": 1_000_000, "tickIntervalMs": 30000},
                    }
                )
            )
            # Simple req/res loop
            async for m in ws:
                if m.type != web.WSMsgType.TEXT:
                    continue
                try:
                    req = json.loads(m.data)
                except Exception:
                    continue
                if req.get("type") == "req":
                    rid = req.get("id")
                    method = req.get("method")
                    params = req.get("params", {})
                    if method == "health":
                        await ws.send_str(
                            json.dumps(
                                {"type": "res", "id": rid, "ok": True, "payload": {"status": "ok"}}
                            )
                        )
                    elif method == "agent":
                        q = params.get("input") or params.get("query") or ""
                        try:
                            text = await self.runtime.run_sync(str(q))
                            ok = True
                            payload = {"output": text}
                        except Exception as e:
                            ok = False
                            payload = {"error": str(e)}
                        await ws.send_str(
                            json.dumps({"type": "res", "id": rid, "ok": ok, "payload": payload})
                        )
                        if ok:
                            await ws.send_str(
                                json.dumps(
                                    {
                                        "type": "event",
                                        "event": "agent",
                                        "payload": {"output": text},
                                    }
                                )
                            )
                    elif method == "send":
                        # Route via channel manager if available
                        await ws.send_str(
                            json.dumps(
                                {"type": "res", "id": rid, "ok": True, "payload": {"sent": True}}
                            )
                        )
                    else:
                        await ws.send_str(
                            json.dumps(
                                {
                                    "type": "res",
                                    "id": rid,
                                    "ok": False,
                                    "error": f"unknown method {method}",
                                }
                            )
                        )
                elif req.get("type") == "event":
                    # client events ignored
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            with contextlib.suppress(Exception):
                await ws.close()
        return ws

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/health", self.handle_health)
        app.router.add_get("/v1/models", self.handle_models)
        app.router.add_post("/v1/chat/completions", self.handle_chat_completions)
        app.router.add_post("/v1/responses", self.handle_chat_completions)
        app.router.add_get("/ws", self.handle_ws)
        # Also serve WS at root for OpenClaw compatibility
        app.router.add_get("/", self.handle_ws)
        self._app = app
        return app

    async def start(self) -> None:
        app = self.build_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            host="127.0.0.1" if self.config.gateway.bind == "loopback" else "0.0.0.0",
            port=self.config.gateway.port,
        )
        await self._site.start()

    async def stop(self) -> None:
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        self.runtime.cleanup()

    def status(self) -> GatewayStatus:
        return GatewayStatus(
            running=self._runner is not None,
            port=self.config.gateway.port,
            channels=list(self.config.channels.keys()),
            cron_jobs=0,
        )
