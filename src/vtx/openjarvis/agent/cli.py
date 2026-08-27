"""CLI for OpenJarvis — `vtx-jarvis` and `vtx openjarvis` subcommand."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

app = typer.Typer(help="OpenJarvis — Hermes/OpenClaw-inspired agent on VTX", no_args_is_help=False)
console = Console()

gateway_app = typer.Typer(help="Gateway control (OpenClaw-style single WS port)")
app.add_typer(gateway_app, name="gateway")

channels_app = typer.Typer(help="Channels")
app.add_typer(channels_app, name="channels")

cron_app = typer.Typer(help="Cron scheduler")
app.add_typer(cron_app, name="cron")

pairing_app = typer.Typer(help="Pairing")
app.add_typer(pairing_app, name="pairing")


@app.command("agent")
def agent_cmd(
    query: str = typer.Argument(..., help="Prompt to run"),
    model: str = typer.Option(None, help="Model override (provider/model)"),
    workspace: str = typer.Option(None, help="Workspace path"),
):
    """Run a single agent turn (VTX-backed)."""
    from vtx.openjarvis.agent.config import OpenJarvisConfig
    from vtx.openjarvis.agent.runtime import OpenJarvisRuntime

    cfg = OpenJarvisConfig.load()
    if workspace:
        cfg.workspace = workspace
    if model:
        if "/" in model:
            cfg.model_provider, cfg.model = model.split("/", 1)
        else:
            cfg.model = model
    rt = OpenJarvisRuntime(cfg, cwd=cfg.workspace)

    async def _run():
        out = await rt.run_sync(query)
        console.print(out or "(no output)")
        rt.cleanup()

    asyncio.run(_run())


@gateway_app.command("start")
def gateway_start(
    port: int = typer.Option(18789, help="Port"),
    foreground: bool = typer.Option(True, "--foreground/--background", help="Run in foreground"),
):
    """Start the multiplexed gateway (WS + HTTP on one port)."""
    from vtx.openjarvis.agent.config import OpenJarvisConfig
    from vtx.openjarvis.server.gateway import OpenJarvisGateway

    cfg = OpenJarvisConfig.load()
    cfg.gateway.port = port
    cfg.save()
    gw = OpenJarvisGateway(cfg)

    async def _serve():
        await gw.start()
        console.print(
            f"[green]OpenJarvis gateway running on {cfg.gateway.bind}:{port}[/green] (WS + HTTP)"
        )
        console.print("Endpoints: /health, /v1/models, /v1/chat/completions, /ws")
        # keep alive
        try:
            while True:
                await asyncio.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            await gw.stop()

    asyncio.run(_serve())


@gateway_app.command("status")
def gateway_status():
    from vtx.openjarvis.agent.config import OpenJarvisConfig

    cfg = OpenJarvisConfig.load()
    console.print(
        f"Gateway port={cfg.gateway.port} bind={cfg.gateway.bind} auth={cfg.gateway.auth_mode}"
    )
    console.print(f"Channels: {list(cfg.channels.keys()) or '(none)'}")
    console.print(f"Workspace: {cfg.workspace}")


@channels_app.command("list")
def channels_list():
    try:
        from vtx.openjarvis.channels.registry import discover_channel_names

        names = discover_channel_names()
        console.print("Built-in channels: " + ", ".join(names))
    except Exception as e:
        console.print(f"[red]discover failed: {e}[/red]")


@channels_app.command("status")
def channels_status():
    from vtx.openjarvis.agent.runtime import OpenJarvisRuntime

    rt = OpenJarvisRuntime()
    cm = rt.channel_manager()
    if cm is None:
        console.print(
            "[yellow]ChannelManager unavailable (missing openjarvis bus/config)[/yellow]"
        )
    else:
        console.print(f"ChannelManager: {cm}")
    rt.cleanup()


@cron_app.command("list")
def cron_list():
    try:
        from pathlib import Path

        from vtx.core.paths import get_config_dir

        # Probe both openjarvis and openjarvis stores
        for p in [Path(get_config_dir()) / "openjarvis" / "cron.json"]:
            if p.exists():
                console.print(p.read_text()[:2000])
                return
        console.print("(no cron store yet)")
    except Exception as e:
        console.print(f"[red]{e}[/red]")


@pairing_app.command("list")
def pairing_list():
    try:
        import vtx.openjarvis.pairing as pairing

        pending = pairing.list_pending()
        console.print(f"Pending: {pending}")
        # get_approved is per-channel; show all channels
        for ch in ["telegram", "discord", "slack", "whatsapp", "matrix"]:
            try:
                approved = pairing.get_approved(ch)
                if approved:
                    console.print(f"Approved {ch}: {approved}")
            except Exception:
                pass
    except Exception as e:
        console.print(f"[red]{e}[/red]")


@pairing_app.command("generate")
def pairing_generate(
    channel: str = typer.Argument(..., help="Channel"),
    sender: str = typer.Argument(..., help="Sender id"),
):
    import vtx.openjarvis.pairing as pairing

    code = pairing.generate_code(channel, sender)
    console.print(f"Pairing code for {sender}@{channel}: [bold]{code}[/bold]")
    console.print(f"Approve via `pairing approve {code}`")


@pairing_app.command("approve")
def pairing_approve(code: str = typer.Argument(...)):
    import vtx.openjarvis.pairing as pairing

    res = pairing.approve_code(code)
    if res:
        console.print(f"[green]Approved {res}[/green]")
    else:
        console.print("[red]Invalid or expired code[/red]")
        raise typer.Exit(code=1)


@app.command("tui")
def tui_cmd(
    model: str = typer.Option(None, help="Model override (provider/model)"),
    cwd: str = typer.Option(None, help="Workspace path"),
):
    """Launch the OpenJarvis TUI (Hermes-style terminal interface)."""
    from vtx.openjarvis.tui.app import OpenJarvisApp

    tui = OpenJarvisApp(cwd=cwd, model=model)
    tui.run()


@app.callback(invoke_without_command=True)
def default_callback(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        # No subcommand -> launch TUI (like `vtx` does)
        from vtx.openjarvis.tui.app import OpenJarvisApp

        tui = OpenJarvisApp()
        tui.run()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
