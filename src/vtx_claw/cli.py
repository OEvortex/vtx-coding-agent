from __future__ import annotations

import argparse
import asyncio
import logging
import multiprocessing
import signal
import sys

from vtx_claw.channels import CHANNEL_REGISTRY
from vtx_claw.config.schema import CHANNEL_FIELD_NAMES, load_claw_config
from vtx_claw.daemon import PIDManager
from vtx_claw.gateway.server import GatewayServer
from vtx_claw.onboard import run_onboard

logger = logging.getLogger("vtx_claw")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="vtx-claw",
        description="VTX messaging gateway",
    )
    sub = parser.add_subparsers(dest="command")

    start_p = sub.add_parser("start", help="Start the gateway")
    start_p.add_argument("--port", type=int, help="Override gateway port")
    start_p.add_argument("--host", type=str, help="Override bind host")
    start_p.add_argument("--verbose", "-v", action="store_true")
    start_p.add_argument("--daemon", "-d", action="store_true", help="Run as background daemon")

    sub.add_parser("stop", help="Stop a running gateway")
    sub.add_parser("status", help="Show gateway status")
    sub.add_parser("onboard", help="Interactive first-time setup")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "start":
        _cmd_start(args)
    elif args.command == "stop":
        _cmd_stop()
    elif args.command == "status":
        _cmd_status()
    elif args.command == "onboard":
        run_onboard()


def _run_gateway(config, pid_manager):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: None)

    server = GatewayServer(config)

    for field_name in CHANNEL_FIELD_NAMES:
        channel_cfg = getattr(config.channels, field_name)
        if channel_cfg.enabled and field_name in CHANNEL_REGISTRY:
            plugin_cls = CHANNEL_REGEGISTRY[field_name]
            server.channel_manager.register(field_name, plugin_cls())
            server.channel_manager.configure(field_name, channel_cfg.model_dump())

    from vtx_claw.channels.web import WebChannel
    server.channel_manager.register("web", WebChannel())
    server.channel_manager.configure("web", {"enabled": True})

    try:
        loop.run_until_complete(server.start())
    except KeyboardInterrupt:
        loop.run_until_complete(server.stop())
    finally:
        loop.close()


def _cmd_start(args: argparse.Namespace) -> None:
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    config = load_claw_config()
    if args.port:
        config.gateway.port = args.port
    if args.host:
        config.gateway.host = args.host

    pid_manager = PIDManager()

    if args.daemon:
        p = multiprocessing.Process(target=_run_gateway, args=(config, pid_manager))
        p.start()
        pid_manager.write(p.pid)
    else:
        _run_gateway(config, pid_manager)


def _cmd_stop() -> None:
    pid = PIDManager().read()
    if pid:
        print(f"vtx-claw: stopping PID {pid}")
        import os
        os.kill(pid, signal.SIGTERM)
        PIDManager().clear()
    else:
        print("vtx-claw: no running instance found")


def _cmd_status() -> None:
    from pathlib import Path

    pid = PIDManager().read()
    print(f"PID: {pid or 'not running'}")

    config_path = Path.home() / ".vtx" / "claw.yml"
    if config_path.exists():
        config = load_claw_config()
        print(f"Gateway: {config.gateway.host}:{config.gateway.port}")
        channels = []
        for field_name in CHANNEL_FIELD_NAMES:
            if getattr(config.channels, field_name).enabled:
                channels.append(field_name)
        print(f"Channels: {', '.join(channels) if channels else 'none'}")
        print(f"Auth: {config.auth.default_policy}")
        print(f"Cron: {'enabled' if config.cron.enabled else 'disabled'}")
        print(f"Sandbox: {'enabled' if config.sandbox.enabled else 'disabled'}")
    else:
        print("No config found. Run: vtx-claw onboard")
