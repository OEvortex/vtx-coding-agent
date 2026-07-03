# Vtx documentation

This folder is the reference companion to the top-level [README](../README.md). The README is the tour; the docs here are the deep dive.

## User docs

| Doc | What it covers |
| --- | --- |
| [configuration.md](configuration.md) | Every YAML config field with its default, validation rule, and CLI override |
| [providers.md](providers.md) | Built-in LLM providers, OAuth and API-key auth, dynamic catalog providers, env vars |
| [tools.md](tools.md) | The 6 core tools + 2 web tools — parameters, mutating flag, and worked examples |
| [permissions.md](permissions.md) | The `prompt`/`auto` modes, the safe-command allowlist, and the decision algorithm |
| [sessions.md](sessions.md) | JSONL session format, resume, handoff, `/export`, compaction, and tree navigation |
| [skills.md](skills.md) | Authoring skills — frontmatter, `$ARGUMENTS`, `register_cmd`, discovery paths |
| [agents.md](agents.md) | Switchable handoff agents — `.vtx/agent/<name>.py`, `AgentAPI`, `Shift+Tab` cycling |
| [theming.md](theming.md) | The full theme catalog and palette tokens |
| [headless.md](headless.md) | The `-p`/`--prompt` non-interactive flow, exit codes, stdin handling |
| [local-models.md](local-models.md) | Tested local models, llama-server setup, model-specific config tuning |
| [storage-layout.md](storage-layout.md) | Every file Vtx touches on disk — config, sessions, models, auth |

## Contributor docs

| Doc | What it covers |
| --- | --- |
| [architecture.md](architecture.md) | Internal module map, message types, request flow, dual agentic loop design |
| [development.md](development.md) | Build, test, lint, typecheck, and release Vtx itself |
| [e2e-test-coverage-review.md](e2e-test-coverage-review.md) | State of the tmux e2e harness and recommended additions |

## vtx-claw docs

vtx-claw is the multi-channel AI agent gateway. Full documentation lives in [`claw/`](claw/README.md).

| Doc | What it covers |
| --- | --- |
| [claw/README.md](claw/README.md) | Overview, quick start, architecture diagram |
| [claw/architecture.md](claw/architecture.md) | Package structure, message bus, agent loop |
| [claw/configuration.md](claw/configuration.md) | Full JSON config reference |
| [claw/cli.md](claw/cli.md) | All CLI commands and subcommands |
| [claw/channels.md](claw/channels.md) | 16 chat platform integrations |
| [claw/tools.md](claw/tools.md) | All agent tools (18+) |
| [claw/slash-commands.md](claw/slash-commands.md) | Built-in slash commands |
| [claw/skills.md](claw/skills.md) | Skills catalog and authoring |
| [claw/providers.md](claw/providers.md) | 50+ LLM providers |
| [claw/gateway.md](claw/gateway.md) | Gateway lifecycle, service installation |
| [claw/sessions.md](claw/sessions.md) | Session storage, compaction, goals |
| [claw/cron.md](claw/cron.md) | Scheduled jobs |
| [claw/security.md](claw/security.md) | SSRF protection, sandboxing |
| [claw/pairing.md](claw/pairing.md) | Device pairing system |
| [claw/webui.md](claw/webui.md) | WebUI backend and WebSocket protocol |
| [claw/audio.md](claw/audio.md) | Audio transcription |
| [claw/mcp.md](claw/mcp.md) | MCP server integration |

## How to read these

- **New to Vtx:** start with the [README](../README.md), then read [configuration.md](configuration.md) and [tools.md](tools.md).
- **Using vtx-claw:** start with [claw/README.md](claw/README.md), then [claw/configuration.md](claw/configuration.md) and [claw/channels.md](claw/channels.md).
- **Adding a provider:** [providers.md](providers.md) and [configuration.md](configuration.md) (the `llm` section).
- **Adding a tool:** [tools.md](tools.md) and [architecture.md](architecture.md) (the `tools/` layer).
- **Adding a skill:** [skills.md](skills.md).
- **Adding a handoff agent:** [agents.md](agents.md).
- **Debugging sessions or compaction:** [sessions.md](sessions.md).
- **Tuning for a local model:** [local-models.md](local-models.md) and [configuration.md](configuration.md) (the `compaction` section).
- **Working on Vtx itself:** [development.md](development.md), then [architecture.md](architecture.md).
- **Working on vtx-claw:** [architecture.md](architecture.md) (dual-loop section), then [claw/architecture.md](claw/architecture.md) and [claw/README.md](claw/README.md).
