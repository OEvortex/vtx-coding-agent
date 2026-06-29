"""CLI for vtx-claw — gateway daemon, TUI, status, and setup.

The ``tui`` command launches vtx's Textual terminal UI (the same TUI
the ``vtx`` CLI uses) so you get a full interactive chat experience
alongside the gateway's channel-based messaging.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import multiprocessing
import os
import signal
import sys

from vtx_claw.channels import CHANNEL_REGISTRY
from vtx_claw.config.schema import CHANNEL_FIELD_NAMES, load_claw_config
from vtx_claw.daemon import PIDManager
from vtx_claw.gateway.server import GatewayServer
from vtx_claw.onboard import run_onboard

logger = logging.getLogger("vtx_claw")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vtx-claw", description="VTX messaging gateway")
    sub = parser.add_subparsers(dest="command")

    # -- start (gateway daemon)
    start_p = sub.add_parser("start", help="Start the gateway")
    start_p.add_argument("--port", type=int, help="Override gateway port")
    start_p.add_argument("--host", type=str, help="Override bind host")
    start_p.add_argument("--verbose", "-v", action="store_true")
    start_p.add_argument("--daemon", "-d", action="store_true", help="Run as background daemon")

    # -- tui (launch vtx's Textual TUI)
    tui_p = sub.add_parser("tui", help="Launch the VTX terminal UI (Textual TUI)")
    tui_p.add_argument("--model", "-m", help="Model to use")
    tui_p.add_argument(
        "--provider", choices=_provider_names(), help="Provider to use (default: from config)"
    )
    tui_p.add_argument("--api-key", "-k", help="API key")
    tui_p.add_argument("--base-url", "-u", help="Base URL for API")
    _add_tui_only_args(tui_p)

    sub.add_parser("stop", help="Stop a running gateway")
    sub.add_parser("status", help="Show gateway status")
    sub.add_parser("onboard", help="Interactive first-time setup")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "start":
        _cmd_start(args)
    elif args.command == "tui":
        _cmd_tui(args)
    elif args.command == "stop":
        _cmd_stop()
    elif args.command == "status":
        _cmd_status()
    elif args.command == "onboard":
        run_onboard()


def _provider_names() -> list[str]:
    """Return sorted provider slugs known to vtx."""
    from vtx.llm import PROVIDER_API_BY_NAME

    return sorted(PROVIDER_API_BY_NAME)


def _add_tui_only_args(parser: argparse.ArgumentParser) -> None:
    """Add arguments shared with vtx's own TUI entrypoint."""
    parser.add_argument(
        "--continue",
        "-c",
        action="store_true",
        dest="continue_recent",
        help="Resume most recent session",
    )
    parser.add_argument(
        "--resume", "-r", dest="resume_session", help="Resume a specific session by ID"
    )
    parser.add_argument(
        "--extension",
        "-e",
        action="append",
        default=[],
        dest="extension_paths",
        metavar="PATH",
        help="Load an extension",
    )
    parser.add_argument(
        "--no-extensions", action="store_true", help="Skip auto-discovered extensions"
    )
    parser.add_argument(
        "--agent", "-a", default=None, metavar="NAME", help="Activate a handoff agent"
    )
    parser.add_argument(
        "--agent-file",
        action="append",
        default=[],
        dest="agent_files",
        metavar="PATH",
        help="Load an agent file",
    )
    parser.add_argument("--no-agents", action="store_true", help="Skip auto-discovered agents")
    parser.add_argument("--goal", default=None, metavar="OBJECTIVE", help="Set a completion goal")
    parser.add_argument(
        "--openai-compat-auth",
        choices=("auto", "required", "none"),
        help="Auth mode for OpenAI-compatible endpoints",
    )
    parser.add_argument(
        "--anthropic-compat-auth",
        choices=("auto", "required", "none"),
        help="Auth mode for Anthropic-compatible endpoints",
    )


def _cmd_tui(args: argparse.Namespace) -> None:
    """Launch the dedicated vtx-claw Textual TUI."""
    from vtx_claw.ui import run_tui

    run_tui(args)


def _run_gateway(config, pid_manager):
    os.environ["VTX_CLAW_DAEMON"] = "1"
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: None)

    server = GatewayServer(config)

    for field_name in CHANNEL_FIELD_NAMES:
        channel_cfg = getattr(config.channels, field_name)
        if channel_cfg.enabled and field_name in CHANNEL_REGISTRY:
            plugin_cls = CHANNEL_REGISTRY[field_name]
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
    logging.basicConfig(level=level, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    config = load_claw_config()
    if args.port:
        config.gateway.port = args.port
    if args.host:
        config.gateway.host = args.host

    pid_manager = PIDManager()

    if args.daemon:
        p = multiprocessing.Process(target=_run_gateway, args=(config, pid_manager))
        p.start()
        if p.pid is not None:
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
