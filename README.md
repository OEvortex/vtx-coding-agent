<div align="center">

<img src="logo.png" alt="Vtx" width="120">

# Vtx

**A coding agent that lives in your terminal.**

[![PyPI version](https://img.shields.io/pypi/v/vtx-coding-agent.svg)](https://pypi.org/project/vtx-coding-agent/)
[![Python](https://img.shields.io/pypi/pyversions/vtx-coding-agent.svg)](https://pypi.org/project/vtx-coding-agent/)
[![GitHub](https://img.shields.io/badge/GitHub-OEvortex%2Fvtx--coding--agent-181717?logo=github)](https://github.com/OEvortex/vtx-coding-agent)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

</div>

---

Vtx is a coding agent harness. Give it a goal and a working folder, and it reads files,
runs commands, edits code, and works through multi-step tasks without stopping to confirm
each one. The same core runs three ways: a keyboard-driven TUI, a headless CLI for
scripts and CI, and a Python SDK for embedding.

It ships 11 packages under the single `vtx-coding-agent` distribution, with 62 providers,
14 tools, and an agent runtime whose every subsystem is replaceable without editing the
harness.

## Quick start

```bash
uv tool install vtx-coding-agent

vtx                              # terminal UI
vtx -p "add tests for vtx.git"   # headless, one task, then exit
```

Python 3.12 or newer. The console script is `vtx`.

Connect a model with `--provider` and `--model`, or export a key and start:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
vtx --provider anthropic --model claude-sonnet-4-5
```

Resume a session with `vtx -c`, or a specific one with `vtx -r <id>`.

## What makes it different

**Everything is a row.** `vtx` boots a *plugin composition*: a profile's ordered bundles,
then its own patch layer, then `--patch` overlays, last write per row id. The tool
registry, the provider seam, the session log, the permission gate, and the agent loop are
all rows, so each is replaceable from configuration rather than by forking the harness.

```sh
vtx --dump-config                          # the composed row list
vtx plugin --profile default disable tool-grep
vtx --profile minimal                      # a four-row composition, for embedding
```

A plugin declares what it needs with `inject` and registers through a context that records
teardown, so unmounting a row unwinds exactly what it registered.

**It extends itself.** Vtx ships a skill that teaches the model which surface to reach for
when it lacks a capability - a directory of instructions, a plugin row that registers a
real tool, or an extension file - and mounts it in place. It will not upgrade the Vtx
package itself; that stays a user action.

**The sandbox is honest about what it is.** A `codemode` script runs in a separate worker
process as you, with the full standard library, the filesystem, subprocesses, and the
network. There is no interpreter-level confinement and none is attempted. What a script
cannot reach is the host's own state, because that lives in another process - so a
deadline is a `kill`, not a cooperative request.

**62 providers, 16 API families.** The catalog ships 62 providers across 16 API families;
`--provider` takes a family name, and dropping a YAML file into `~/.vtx/providers/` adds
your own.

**Sessions are durable and branchable.** Resume the last one, fork a new one mid-task to
explore a different approach, and revert a turn with snapshots.

## Documentation

Full reference, generated from the source:

| | |
|---|---|
| **[Quickstart](docs/quickstart.md)** | Install, connect a model, finish a first task |
| **[How Vtx works](docs/how-vtx-works.md)** | The composition, the agent loop, how a tool reaches the model |
| **[Tools](docs/tools.md)** | Every tool, its exact parameter schema, and worked examples |
| **[CLI](docs/cli.md)** | Every flag, subcommand, and exit code |
| **[Configuration](docs/configuration.md)** | The config file and every field |
| **[Providers](docs/providers.md)** | The catalog, auth modes, OAuth, local and custom models |

**Using it**

| | |
|---|---|
| [Slash commands](docs/slash-commands.md) | [Keybindings](docs/keybindings.md) | [Theming](docs/theming.md) |
| [Sessions](docs/sessions.md) | [Goals](docs/goals.md) | [Permissions](docs/permissions.md) |
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
| [SDK](docs/sdk.md) | Run Vtx inside your own application |
| [Development](docs/development.md) | Repo layout, the lint and test commands, how to add a tool or a row |
| [Storage layout](docs/storage-layout.md) | Every path Vtx writes to on disk |

## Packages

| Package | Description |
|---------|-------------|
| **[@vtx.plugins](src/vtx/plugins)** | The plugin kernel: `Context`, `EventHub`, plugin rows, patch layers, profiles, boot composition |
| **[@vtx.protocol](src/vtx/protocol)** | Leaf. Message, stream, and tool-contract types. Imports nothing from `vtx` |
| **[@vtx.telemetry](src/vtx/telemetry)** | Leaf. Spans, processors, console and JSONL exporters |
| **[@vtx.git](src/vtx/git)** | Git and GitHub integration: `gh` CLI wrapper, GitHub App auth |
| **[@vtx.core](src/vtx/core)** | Agent events, permission gate, compaction, config schema and paths, theme registry |
| **[@vtx.ai](src/vtx/ai)** | The LLM layer: provider catalog, OAuth, SDK adapters, model catalog, tool parsing |
| **[@vtx.codemode](src/vtx/codemode)** | The script sandbox and its tool-discovery layer |
| **[@vtx.mcp](src/vtx/mcp)** | MCP client: server config, transports, tool exposure, trust prompt |
| **[@vtx.tui](src/vtx/tui)** | Base terminal-UI toolkit: editing, fuzzy matching, overlays, block renderers |
| **[@vtx.agent](src/vtx/agent)** | Product-neutral harness: loop, turn engine, sessions, tool registry, prompts, hooks, goals, SDK |
| **[@vtx.coding_agent](src/vtx/coding_agent)** | Product layer: CLI entry point, headless runner, filesystem tools, the TUI app |

Dependencies flow `protocol` and `telemetry` into `core`, `git`, and `ai`; those into
`agent`; `agent` into `coding_agent`, with `mcp`, `tui`, and `plugins` above. Each package
carries its own README with the public surface and internal notes.

**Twelve tools are on by default** - `read`, `edit`, `write`, `bash`, `find`, `skill`,
`web`, `ask_user`, `tool_search`, `codemode`, `goal`, `delegate_subagent` - resolved by the
composition, not by a constant. `grep` and `web_search` are registered but off. The
sub-agent tool is defined internally as `task` and exposed as `delegate_subagent`. Agent
profiles load only from `.vtx/agent/<name>.py`; none ship built in. Configuration lives in
`~/.vtx/config.yml`.

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
