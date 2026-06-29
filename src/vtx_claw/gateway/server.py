from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from aiohttp import web

from vtx_claw.agent import AgentHandler
from vtx_claw.events import EventBus, InboundEvent
from vtx_claw.gateway.channel_manager import ChannelManager
from vtx_claw.memory import MemoryManager
from vtx_claw.sessions import SessionManager
from vtx_claw.web_ui import register_web_ui_routes

logger = logging.getLogger(__name__)


class GatewayServer:
    def __init__(self, config: Any) -> None:
        self.config = config
        self.channel_manager = ChannelManager()
        self.event_bus = EventBus()
        self.session_manager = SessionManager()
        self.memory_manager = MemoryManager()
        self.agent_handler = AgentHandler(self.session_manager, config)
        self._connections: dict[str, web.WebSocketResponse] = {}
        self._running = False
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        self._running = True
        self._app = web.Application()
        self._setup_routes()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        site = web.TCPSite(self._runner, self.config.gateway.host, self.config.gateway.port)

        self.event_bus.subscribe(InboundEvent, self._on_inbound)
        await self.event_bus.start()

        self.agent_handler.session_manager = self.session_manager

        channel_results = await self.channel_manager.start_all()
        started = sum(1 for v in channel_results.values() if v)
        logger.info("Started %d channel(s)", started)

        if self.config.cron.enabled:
            from vtx_claw.cron.scheduler import CronJobManager

            self._cron_manager = CronJobManager()
            self._cron_manager.set_executor(self._on_cron_job)
            await self._cron_manager.start()
            logger.info("Cron scheduler started")

        await site.start()
        logger.info(
            "Gateway running on http://%s:%s", self.config.gateway.host, self.config.gateway.port
        )

        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        if hasattr(self, "_cron_manager"):
            await self._cron_manager.stop()
        await self.channel_manager.stop_all()
        await self.event_bus.stop()
        if self._runner:
            await self._runner.cleanup()
        logger.info("Gateway stopped")

    def _setup_routes(self) -> None:
        assert self._app is not None
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/ws", self._handle_websocket)
        self._app.router.add_get("/v1/channels", self._handle_list_channels)
        self._app.router.add_get("/v1/sessions", self._handle_list_sessions)
        self._app.router.add_get("/v1/memory/{user_id}", self._handle_get_memory)
        self._app.router.add_post("/v1/chat", self._handle_chat)
        self._app.router.add_post("/v1/cron", self._handle_add_cron)
        self._app.router.add_delete("/v1/cron/{name}", self._handle_delete_cron)
        self._app.router.add_get("/v1/cron", self._handle_list_cron)
        register_web_ui_routes(self._app)

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "running": self._running})

    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        conn_id = str(id(ws))
        self._connections[conn_id] = ws
        logger.info("WebSocket connected: %s", conn_id)

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    await self._handle_ws_message(conn_id, ws, msg.data)
                elif msg.type == web.WSMsgType.ERROR:
                    logger.error("WebSocket error: %s", ws.exception())
        finally:
            self._connections.pop(conn_id, None)
            logger.info("WebSocket disconnected: %s", conn_id)

        return ws

    async def _handle_ws_message(self, conn_id: str, ws: web.WebSocketResponse, data: str) -> None:
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            await ws.send_str(json.dumps({"error": "Invalid JSON"}))
            return

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "connect":
            await ws.send_str(
                json.dumps(
                    {
                        "type": "res",
                        "id": msg_id,
                        "ok": True,
                        "payload": {
                            "protocol": 1,
                            "server": {"name": "vtx-claw", "version": "0.1.0"},
                        },
                    }
                )
            )
        elif method == "chat":
            text = params.get("text", "")
            session_id = params.get("session_id", conn_id)
            event = InboundEvent(
                type="inbound",
                session_id=session_id,
                channel="web",
                user_id=conn_id,
                text=text,
                timestamp=time.time(),
            )
            await self.event_bus.publish(event)
            await ws.send_str(
                json.dumps(
                    {"type": "res", "id": msg_id, "ok": True, "payload": {"status": "processing"}}
                )
            )
        elif method == "channels.list":
            await ws.send_str(
                json.dumps(
                    {
                        "type": "res",
                        "id": msg_id,
                        "ok": True,
                        "payload": {"channels": self.channel_manager.list_all()},
                    }
                )
            )
        elif method == "sessions.list":
            sessions = self.session_manager.list_all()
            await ws.send_str(
                json.dumps(
                    {
                        "type": "res",
                        "id": msg_id,
                        "ok": True,
                        "payload": {"sessions": [s.to_dict() for s in sessions]},
                    }
                )
            )
        else:
            await ws.send_str(
                json.dumps(
                    {
                        "type": "res",
                        "id": msg_id,
                        "ok": False,
                        "error": {"code": "METHOD_NOT_FOUND", "message": f"Unknown: {method}"},
                    }
                )
            )

    async def _handle_list_channels(self, request: web.Request) -> web.Response:
        return web.json_response({"channels": self.channel_manager.list_all()})

    async def _handle_list_sessions(self, request: web.Request) -> web.Response:
        sessions = self.session_manager.list_all()
        return web.json_response({"sessions": [s.to_dict() for s in sessions]})

    async def _handle_get_memory(self, request: web.Request) -> web.Response:
        user_id = request.match_info["user_id"]
        memories = self.memory_manager.get_all(user_id)
        return web.json_response({"user_id": user_id, "memories": memories})

    async def _handle_chat(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        text = body.get("text", "")
        session_id = body.get("session_id", "http")
        user_id = body.get("user_id", "http-user")

        event = InboundEvent(
            type="inbound",
            session_id=session_id,
            channel="http",
            user_id=user_id,
            text=text,
            timestamp=time.time(),
        )
        await self.event_bus.publish(event)
        return web.json_response({"status": "processing", "session_id": session_id})

    async def _handle_add_cron(self, request: web.Request) -> web.Response:
        if not hasattr(self, "_cron_manager"):
            return web.json_response({"error": "Cron not enabled"}, status=400)
        try:
            body = await request.json()
            from vtx_claw.cron.scheduler import CronJob

            job = CronJob(
                name=body["name"],
                schedule=body["schedule"],
                prompt=body["prompt"],
                channel=body.get("channel", ""),
                user_id=body.get("user_id", ""),
            )
            self._cron_manager.add_job(job)
            return web.json_response({"status": "ok", "job": body["name"]})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_delete_cron(self, request: web.Request) -> web.Response:
        if not hasattr(self, "_cron_manager"):
            return web.json_response({"error": "Cron not enabled"}, status=400)
        name = request.match_info["name"]
        removed = self._cron_manager.remove_job(name)
        return web.json_response({"removed": removed})

    async def _handle_list_cron(self, request: web.Request) -> web.Response:
        if not hasattr(self, "_cron_manager"):
            return web.json_response({"jobs": []})
        jobs = self._cron_manager.list_jobs()
        return web.json_response(
            {
                "jobs": [
                    {
                        "name": j.name,
                        "schedule": j.schedule,
                        "prompt": j.prompt,
                        "enabled": j.enabled,
                    }
                    for j in jobs
                ]
            }
        )

    async def _on_inbound(self, event: InboundEvent) -> None:
        logger.info("Inbound from %s/%s: %s", event.channel, event.user_id, event.text[:80])

        if "remember" in event.text.lower():
            parts = event.text.split(" ", 1)
            if len(parts) > 1:
                self.memory_manager.remember(event.user_id, "preference", parts[1])
                await self.channel_manager.send_message(
                    event.channel, event.user_id, "Got it! I'll remember that."
                )
                return

        response = await self.agent_handler.handle(event)
        if response:
            await self.channel_manager.send_message(event.channel, event.user_id, response)

        await self.broadcast_event(
            "agent", {"data": {"text": response, "phase": "end"}, "session_id": event.session_id}
        )

    async def _on_cron_job(self, job: Any) -> None:
        logger.info("Running cron job: %s", job.name)
        event = InboundEvent(
            type="inbound",
            session_id=f"cron:{job.name}",
            channel=job.channel or "system",
            user_id=job.user_id or "cron",
            text=job.prompt,
            timestamp=time.time(),
        )
        await self.event_bus.publish(event)

    async def broadcast_event(self, event_type: str, data: Any) -> None:
        frame = json.dumps({"type": "event", "event": event_type, "data": data})
        dead = []
        for conn_id, ws in self._connections.items():
            try:
                await ws.send_str(frame)
            except Exception:
                dead.append(conn_id)
        for conn_id in dead:
            self._connections.pop(conn_id, None)
