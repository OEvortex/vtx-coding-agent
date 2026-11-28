<div align="center">

<img src="logo.png" alt="Ctx" width="120">

# Ctx

**A coding agent that lives in your terminal.**

[![PyPI version](https://img.shields.io/pypi/v/ctx-code.svg)](https://pypi.org/project/ctx-code/)
[![Python](https://img.shields.io/pypi/pyversions/ctx-code.svg)](https://pypi.org/project/ctx-code/)
[![GitHub](https://img.shields.io/badge/GitHub-OEvortex%2Fctx--coding--agent-181717?logo=github)](https://github.com/OEvortex/ctx-code)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

</div>

---

Ctx is a coding agent harness. Give it a goal and a working folder, and it reads files,
runs commands, edits code, and works through multi-step tasks without stopping to confirm
each one. The same core runs three ways: a keyboard-driven TUI, a headless CLI for
scripts and CI, and a Python SDK for embedding.

It ships 11 packages under the single `ctx-code` distribution, with 65 providers,
and an agent runtime whose every subsystem is replaceable without editing the
harness.

## Quick start

```bash
uv tool install ctx-code

ctx                              # terminal UI
ctx -p "add tests for ctx.git"   # headless, one task, then exit
```

Python 3.12 or newer. The console script is `ctx`.

Connect a model with `--provider` and `--model`, or export a key and start:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
ctx --provider anthropic --model claude-sonnet-4-20250514
```

Resume a session with `ctx -c`, or a specific one with `ctx -r <id>`.

## What makes it different

**Everything is a row.** `ctx` boots a *plugin composition*: a profile's ordered bundles,
then its own patch layer, then `--patch` overlays, last write per row id. The tool
registry, the provider seam, the session log, the permission gate, and the agent loop are
all rows, so each is replaceable from configuration rather than by forking the harness.

A plugin declares what it needs with `inject` and registers through a context that records
teardown, so unmounting a row unwinds exactly what it registered.

**It extends itself.** Ctx ships a skill that teaches the model which surface to reach for
when it lacks a capability - a directory of instructions, a plugin row that registers a
real tool, or an extension file - and mounts it in place. It will not upgrade the Ctx
package itself; that stays a user action.

**The sandbox is honest about what it is.** A `codemode` script runs in a separate worker
process as you, with the full standard library, the filesystem, subprocesses, and the
network. There is no interpreter-level confinement and none is attempted. What a script
cannot reach is the host's own state, because that lives in another process - so a
deadline is a `kill`, not a cooperative request.

**65 providers, 3 API families.** The catalog ships 65 providers (60 from `provider.yaml`
plus 5 OAuth built-ins) across 3 API families (`anthropic`, `devin`, `openai_compat`);
`--provider` takes a provider slug, and dropping a YAML file into `~/.ctx/providers/`
(or project-local `.ctx/providers/`) adds your own.

**Sessions are durable and branchable.** Resume the last one, fork a new one mid-task to
explore a different approach, and revert a turn with snapshots.

## Documentation

Full reference, generated from the source:

| | |
|---|---|
| **[Quickstart](docs/quickstart.md)** | Install, connect a model, finish a first task |
| **[How Ctx works](docs/how-ctx-works.md)** | The composition, the agent loop, how a tool reaches the model |
| **[Tools](docs/tools.md)** | Every tool, its exact parameter schema, and worked examples |
| **[CLI](docs/cli.md)** | Every flag, subcommand, and exit code |
| **[Configuration](docs/configuration.md)** | The config file and every field |
| **[Providers](docs/providers.md)** | The catalog, auth modes, OAuth, local and custom models |

**Using it**

| | |
|---|---|
| [Slash commands](docs/slash-commands.md) | [Keybindings](docs/keybindings.md) | [Theming](docs/theming.md) |
| [Sessions](docs/sessions.md) | [Permissions](docs/permissions.md) |
| [Agents & sub-agents](docs/agents.md) | [Headless / CI](docs/headless.md) | [MCP](docs/mcp.md) |

**Extending it**

| | |
|---|---|
| [Skills](docs/skills.md) | A directory of instructions the model loads on demand |
| [Plugins](docs/plugins.md) | A row that can register a tool, command, hook, or replace a core service |
| [Extensions](docs/extensions.md) | The `ExtensionAPI` surface, for providers and renderers |
| [Codemode](docs/codemode.md) | The sandbox and its tool-exposure model |

**Building on it**

| | |
|---|---|
| [SDK](docs/sdk.md) | Run Ctx inside your own application |
| [Development](docs/development.md) | Repo layout, the lint and test commands, how to add a tool or a row |
| [Storage layout](docs/storage-layout.md) | Every path Ctx writes to on disk |

## Packages

| Package | Description |
|---------|-------------|
| **[@ctx.plugins](src/ctx/plugins)** | The plugin kernel: `Context`, `EventHub`, plugin rows, patch layers, profiles, boot composition |
| **[@ctx.protocol](src/ctx/protocol)** | Leaf. Message, stream, and tool-contract types. Imports nothing from `ctx` |
| **[@ctx.telemetry](src/ctx/telemetry)** | Leaf. Spans, processors, console and JSONL exporters |
| **[@ctx.git](src/ctx/git)** | Git and GitHub integration: `gh` CLI wrapper, GitHub App auth |
| **[@ctx.core](src/ctx/core)** | Agent events, permission gate, compaction, config schema and paths, theme registry |
| **[@ctx.ai](src/ctx/ai)** | The LLM layer: provider catalog, OAuth, SDK adapters, model catalog, tool parsing |
| **[@ctx.codemode](src/ctx/codemode)** | The script sandbox and its tool-discovery layer |
| **[@ctx.mcp](src/ctx/mcp)** | MCP client: server config, transports, tool exposure, trust prompt |
| **[@ctx.term](src/ctx/term)** | Base terminal-UI toolkit: editing, fuzzy matching, overlays, block renderers |
| **[@ctx.agent](src/ctx/agent)** | Product-neutral harness: loop, turn engine, sessions, tool registry, prompts, hooks, goals, SDK |
| **[@ctx.code](src/ctx/code)** | Product layer: CLI entry point, headless runner, filesystem tools, the TUI app |
| **[vx](packages/vx)** | Standalone `vx` distribution: a minimal coding agent on the ctx harness with its own TUI, swarm tools, and headless mode |

Dependencies flow `protocol` and `telemetry` into `core`, `git`, and `ai`; those into
`agent`; `agent` into `code`, with `mcp`, `tui`, and `plugins` above. Each package
carries its own README with the public surface and internal notes.

**Tools in the base composition** include `read`, `apply-patch` (writes go through
`apply-patch`; there is no separate `write` or `edit` tool), `bash`, `grep`, `skill`,
`todo`, `web`, `ask_user`, `codemode`, and `subagent` (the delegate/sub-agent tool,
defined internally as `task`/`TaskTool`). `web_search` is registered but not on by
default, and `find` is not a registered row (use `bash` for file discovery). All are
resolved by the composition, not by a constant. Agent profiles load from
`.ctx/agent/<name>.py` and `~/.ctx/agent/`; none ship built in. Configuration lives in
`~/.ctx/config.yml`.

## Contributing

[AGENTS.md](AGENTS.md) holds the project rules for humans and agents alike. In short:

```bash
uv run ruff format .
uv run ruff check .
uvx ty check .
uv run python -m pytest path/to/test_file.py    # one file, not the full suite
```

Run a single file's tests rather than the full suite; it is slow and resource intensive.
See [docs/development.md](docs/development.md).

## License

[Apache License 2.0](LICENSE)
