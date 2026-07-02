# vtx-claw

vtx-claw is the multi-channel AI agent gateway. It runs as a persistent background process, connecting your AI agent to chat platforms (Telegram, Discord, Slack, WhatsApp, Matrix, etc.) and a built-in WebUI simultaneously.

Unlike the `vtx` TUI (which runs interactively in your terminal), `vtx-claw` operates headlessly — managing sessions, memory, scheduling, and multi-user access across all connected channels.

## Quick Start

```bash
# Install
pip install vtx-coding-agent

# First-time setup
vtx-claw onboard --wizard

# Start the gateway (all channels + WebUI)
vtx-claw gateway
```

The WebUI opens at `http://127.0.0.1:8765`. Enabled chat channels start automatically.

## Documentation

| Doc | What it covers |
| --- | --- |
| [architecture.md](architecture.md) | Package structure, message bus, agent loop, module map |
| [configuration.md](configuration.md) | Full JSON config reference with all fields and defaults |
| [cli.md](cli.md) | Every CLI command, subcommand, and flag |
| [channels.md](channels.md) | All 16 chat platform integrations |
| [tools.md](tools.md) | All agent tools (exec, search, files, messaging, subagents) |
| [slash-commands.md](slash-commands.md) | All built-in in-chat slash commands |
| [skills.md](skills.md) | Built-in skills catalog and custom skill authoring |
| [providers.md](providers.md) | 50+ supported LLM providers and model presets |
| [gateway.md](gateway.md) | Gateway lifecycle, service installation, heartbeat |
| [sessions.md](sessions.md) | Session storage, compaction, goals, forking |
| [cron.md](cron.md) | Scheduled job system |
| [security.md](security.md) | SSRF protection and workspace sandboxing |
| [pairing.md](pairing.md) | DM device pairing system |
| [webui.md](webui.md) | WebUI backend, WebSocket protocol, settings API |
| [audio.md](audio.md) | Audio transcription providers |
| [mcp.md](mcp.md) | MCP server integration |

## Architecture at a Glance

```
┌─────────────────────────────────────────────────┐
│                  vtx-claw gateway                │
│                                                  │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐    │
│  │ Telegram  │   │ Discord  │   │ WebUI    │    │
│  │ Channel   │   │ Channel  │   │ (WS)     │    │
│  └─────┬────┘   └─────┬────┘   └─────┬────┘    │
│        │              │              │           │
│        └──────┬───────┴──────┬───────┘           │
│               ▼              ▼                   │
│        ┌──────────┐   ┌──────────┐              │
│        │ Inbound  │   │ Outbound │              │
│        │  Queue   │──▶│  Queue   │              │
│        └─────┬────┘   └────▲─────┘              │
│              ▼              │                    │
│        ┌─────────────────────────┐              │
│        │      Agent Loop         │              │
│        │  (LLM + Tools + Memory) │              │
│        └─────────────────────────┘              │
└─────────────────────────────────────────────────┘
```
