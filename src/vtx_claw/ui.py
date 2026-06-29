"""Dedicated TUI for vtx-claw — status dashboard, gateway controls, and chat client."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from typing import Any

import aiohttp
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, RichLog, Static

from vtx_claw.agent import AgentHandler
from vtx_claw.config.schema import CHANNEL_FIELD_NAMES, load_claw_config
from vtx_claw.daemon import PIDManager
from vtx_claw.events import InboundEvent

logger = logging.getLogger("vtx_claw.ui")


class ClawDashboard(App[None]):
    TITLE = "Vtx-Claw Gateway Manager"
    SUB_TITLE = "Dashboard & Client"

    CSS = """
    Screen {
        background: #11111b;
    }

    #sidebar {
        width: 32;
        background: #1e1e2e;
        border-right: tall #313244;
        padding: 1 2;
    }

    #chat-container {
        background: #11111b;
        padding: 1 2;
    }

    .title {
        text-align: center;
        text-style: bold;
        color: #89b4fa;
        margin-bottom: 1;
    }

    .section-title {
        text-style: bold;
        color: #cdd6f4;
        margin-top: 1;
        margin-bottom: 0;
    }

    .status-text {
        color: #a6adc8;
        margin-bottom: 1;
    }

    .btn {
        width: 100%;
        margin-bottom: 1;
        background: #313244;
        color: #cdd6f4;
    }

    #btn-start {
        background: #a6e3a1;
        color: #11111b;
    }

    #btn-stop {
        background: #f38ba8;
        color: #11111b;
    }

    #chat-log {
        height: 1fr;
        background: #181825;
        border: tall #313244;
        margin-bottom: 1;
    }

    #chat-input {
        background: #1e1e2e;
        border: tall #313244;
        color: #cdd6f4;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.config = load_claw_config()
        self.pid_manager = PIDManager()
        self.ws_session: aiohttp.ClientSession | None = None
        self.ws_conn: aiohttp.ClientWebSocketResponse | None = None
        self.ws_task: asyncio.Task | None = None
        self.local_agent: AgentHandler | None = None
        self.msg_id = 0
        self.pending_responses: dict[int, asyncio.Future[Any]] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Static("VTX-CLAW", classes="title")
                yield Static("GATEWAY STATUS", classes="section-title")
                yield Static("Checking...", id="gateway-status", classes="status-text")
                yield Button("Start Gateway", id="btn-start", classes="btn")
                yield Button("Stop Gateway", id="btn-stop", classes="btn")
                yield Button("Onboard Setup", id="btn-onboard", classes="btn")

                yield Static("CHANNELS", classes="section-title")
                yield Static("Loading channels...", id="channels-list", classes="status-text")

                yield Static("CONFIG", classes="section-title")
                yield Static("Loading config...", id="config-info", classes="status-text")

            with Vertical(id="chat-container"):
                yield RichLog(id="chat-log", wrap=True, highlight=True, markup=True)
                yield Input(placeholder="Type message or /command...", id="chat-input")
        yield Footer()

    async def on_mount(self) -> None:
        chat_log = self.query_one("#chat-log", RichLog)
        chat_log.write("[bold cyan]Welcome to the Vtx-Claw Dashboard![/bold cyan]")
        chat_log.write("Use the input box to chat with the agent or run commands.")
        chat_log.write(
            "Available commands: [bold]/start[/bold], [bold]/stop[/bold], "
            "[bold]/status[/bold], [bold]/clear[/bold], [bold]/exit[/bold]"
        )
        chat_log.write("-" * 40)

        # Periodically refresh status
        self.set_interval(2.0, self.refresh_status)
        await self.refresh_status()

        # Connect to WebSocket in background
        self.run_worker(self.connect_websocket())

    async def refresh_status(self) -> None:
        # Check process
        pid = self.pid_manager.read()
        is_running = False
        if pid:
            try:
                os.kill(pid, 0)
                is_running = True
            except OSError:
                pass

        # Update TUI sidebar status
        status_widget = self.query_one("#gateway-status", Static)
        start_btn = self.query_one("#btn-start", Button)
        stop_btn = self.query_one("#btn-stop", Button)

        if is_running:
            status_widget.update(
                f"[bold green]Running[/bold green]\nPID: {pid}\n"
                f"http://{self.config.gateway.host}:{self.config.gateway.port}"
            )
            start_btn.disabled = True
            stop_btn.disabled = False
        else:
            status_widget.update("[bold red]Stopped[/bold red]")
            start_btn.disabled = False
            stop_btn.disabled = True

        # Channels info
        channels_widget = self.query_one("#channels-list", Static)
        enabled_channels = []
        for field_name in CHANNEL_FIELD_NAMES:
            if getattr(self.config.channels, field_name).enabled:
                enabled_channels.append(f"• {field_name}")
        channels_widget.update(
            "\n".join(enabled_channels) if enabled_channels else "No channels enabled"
        )

        # Config info
        config_widget = self.query_one("#config-info", Static)
        sandbox_str = "Enabled" if self.config.sandbox.enabled else "Disabled"
        cron_str = "Enabled" if self.config.cron.enabled else "Disabled"
        config_widget.update(
            f"Auth: {self.config.auth.default_policy}\n"
            f"Sandbox: {sandbox_str}\n"
            f"Cron: {cron_str}\n"
            f"Model: {self.config.llm.default_model or 'default'}"
        )

    async def connect_websocket(self) -> None:
        # Clean up any existing connection
        await self.disconnect_websocket()

        url = f"http://{self.config.gateway.host}:{self.config.gateway.port}/ws"
        chat_log = self.query_one("#chat-log", RichLog)

        try:
            self.ws_session = aiohttp.ClientSession()
            self.ws_conn = await self.ws_session.ws_connect(url)
            chat_log.write("[green]✓ Connected to background gateway WebSocket.[/green]")

            # Send connect protocol handshake
            self.msg_id += 1
            await self.ws_conn.send_json({"method": "connect", "id": self.msg_id, "params": {}})

            # Read messages
            async for msg in self.ws_conn:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = msg.json()
                    await self.handle_ws_incoming(data)
        except Exception:
            chat_log.write(
                "[yellow]⚠ Gateway WebSocket not connected. "
                "Messaging will run via in-process local fallback.[/yellow]"
            )
            await self.disconnect_websocket()

    async def disconnect_websocket(self) -> None:
        if self.ws_conn:
            await self.ws_conn.close()
            self.ws_conn = None
        if self.ws_session:
            await self.ws_session.close()
            self.ws_session = None

    async def handle_ws_incoming(self, data: dict[str, Any]) -> None:
        chat_log = self.query_one("#chat-log", RichLog)
        msg_type = data.get("type")

        if msg_type == "res":
            ok = data.get("ok", False)
            if not ok:
                err = data.get("error", {})
                chat_log.write(f"[red]Error ({err.get('code')}): {err.get('message')}[/red]")
        elif msg_type == "event":
            event_name = data.get("event")
            event_data = data.get("data", {})
            if event_name == "agent":
                agent_data = event_data.get("data", {})
                text = agent_data.get("text")
                phase = agent_data.get("phase")
                if text and phase == "end":
                    chat_log.write(f"[bold magenta]Claw:[/bold magenta] {text}")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        chat_log = self.query_one("#chat-log", RichLog)

        if btn_id == "btn-start":
            chat_log.write("Starting gateway daemon...")
            subprocess.Popen(["vtx-claw", "start", "--daemon"])
            await asyncio.sleep(1.0)
            self.run_worker(self.connect_websocket())
        elif btn_id == "btn-stop":
            chat_log.write("Stopping gateway daemon...")
            subprocess.Popen(["vtx-claw", "stop"])
            await self.disconnect_websocket()
        elif btn_id == "btn-onboard":
            chat_log.write(
                "To run onboard, exit dashboard TUI and run: [bold]vtx-claw onboard[/bold]"
            )

        await self.refresh_status()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return

        input_widget = self.query_one("#chat-input", Input)
        input_widget.value = ""

        chat_log = self.query_one("#chat-log", RichLog)

        # Handle commands
        if text.startswith("/"):
            cmd = text[1:].strip().lower()
            if cmd in ("exit", "quit"):
                self.exit()
            elif cmd == "clear":
                chat_log.clear()
            elif cmd == "status":
                await self.refresh_status()
                chat_log.write("Status refreshed.")
            elif cmd == "start":
                chat_log.write("Starting gateway daemon...")
                subprocess.Popen(["vtx-claw", "start", "--daemon"])
                await asyncio.sleep(1.0)
                self.run_worker(self.connect_websocket())
            elif cmd == "stop":
                chat_log.write("Stopping gateway daemon...")
                subprocess.Popen(["vtx-claw", "stop"])
                await self.disconnect_websocket()
            else:
                chat_log.write(f"[red]Unknown command: /{cmd}[/red]")
            return

        chat_log.write(f"[bold green]You:[/bold green] {text}")

        # Send via WebSocket if connected
        if self.ws_conn and not self.ws_conn.closed:
            self.msg_id += 1
            await self.ws_conn.send_json(
                {
                    "method": "chat",
                    "id": self.msg_id,
                    "params": {"text": text, "session_id": "claw-tui-session"},
                }
            )
        else:
            # Fallback to local in-process execution
            chat_log.write("[dim](Running local fallback agent session...)[/dim]")
            self.run_worker(self.run_local_agent_turn(text))

    async def run_local_agent_turn(self, text: str) -> None:
        chat_log = self.query_one("#chat-log", RichLog)
        agent = self.local_agent
        if not agent:
            try:
                agent = AgentHandler(self.config)
                self.local_agent = agent
            except Exception as e:
                chat_log.write(f"[red]Failed to initialize local fallback agent: {e}[/red]")
                return

        try:
            event = InboundEvent(
                type="inbound",
                session_id="tui-local",
                channel="tui",
                user_id="user",
                text=text,
                timestamp=time.time(),
            )
            response = await agent.handle(event)
            chat_log.write(f"[bold magenta]Claw:[/bold magenta] {response}")
        except Exception as e:
            chat_log.write(f"[red]Local agent execution error: {e}[/red]")

    async def action_exit(self) -> None:
        await self.disconnect_websocket()
        self.exit()


def run_tui(args: Any = None) -> None:
    app = ClawDashboard()
    app.run()
