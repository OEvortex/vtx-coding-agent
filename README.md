# Vtx

A coding agent harness: a keyboard-driven terminal UI, a headless CLI for scripts and CI, and a Python SDK, built on one agent runtime that ships ten packages under a single `vtx-coding-agent` distribution. The base system prompt is 665 tokens.

* **[`vtx.coding_agent`](src/vtx/coding_agent)**: Product layer. CLI entry point, headless runner, filesystem tools, the TUI app
* **[`vtx.agent`](src/vtx/agent)**: Product-neutral harness. Agent loop, turn engine, sessions, tool registry, prompts, extensions, goals, SDK
* **[`vtx.ai`](src/vtx/ai)**: LLM layer. Provider catalog, OAuth, SDK adapters, model catalog, tool parsing

To learn more:

* [Read the documentation](docs/README.md), or start from [docs/index.md](docs/index.md) for a tour by audience
* [Read the architecture map](docs/architecture.md) for the ten packages and their dependency direction
* Ask the agent to explain itself

## All Packages

| Package | Description |
|---------|-------------|
| **[@vtx.protocol](src/vtx/protocol)** | Leaf. Message, stream, and tool-contract types. Imports nothing from `vtx` |
| **[@vtx.telemetry](src/vtx/telemetry)** | Leaf. Spans, processors, console and JSONL exporters |
| **[@vtx.git](src/vtx/git)** | Git and GitHub integration: `gh` CLI wrapper, GitHub App auth |
| **[@vtx.core](src/vtx/core)** | Agent events, permission gate, compaction, config schema and paths, theme registry |
| **[@vtx.ai](src/vtx/ai)** | The LLM layer: provider catalog, OAuth, SDK adapters, model catalog, tool parsing |
| **[@vtx.codemode](src/vtx/codemode)** | The script sandbox and its tool discovery layer |
| **[@vtx.agent](src/vtx/agent)** | Product-neutral harness: loop, turn engine, sessions, tool registry, prompts, extensions, goals, SDK |
| **[@vtx.mcp](src/vtx/mcp)** | MCP client: server config, transports, tool exposure, trust prompt |
| **[@vtx.tui](src/vtx/tui)** | Base terminal-UI toolkit: editing, fuzzy matching, overlays, block renderers. Knows nothing about the agent |
| **[@vtx.coding_agent](src/vtx/coding_agent)** | Product layer: CLI entry point, headless runner, concrete filesystem tools, the TUI app |

Dependencies flow `protocol` and `telemetry` into `core`, `git`, and `ai`; those into `agent`; `agent` into `coding_agent`, with `mcp` and `tui` sitting above. Each package carries its own README with the public surface and internal notes.

Ten tools are registered by default: `read`, `edit`, `write`, `bash`, `find`, `skill`, `web`, `ask_user`, `delegate_subagent`, `goal`. The harness also registers `codemode` and `tool_search`. The sub-agent tool is defined internally as `task` and exposed to the model under the name `delegate_subagent`. Agent profiles are only ever loaded from `.vtx/agent/<name>.py`; none ship built in. There are 62 built-in providers: 59 declared in `src/vtx/ai/provider.yaml` plus `github-copilot`, `codex`, and `cline`, which are constructed in code because they need host OAuth. Configuration lives in `~/.vtx/config.yml`.

## Install

```bash
uv tool install vtx-coding-agent
vtx                              # terminal UI
vtx -p "add tests for vtx.git"   # headless, one task
```

Python 3.12 or newer. The console script is `vtx`.

## Permissions

Vtx gates the mutating tools (`bash`, `edit`, `write`) behind a permission mode. `prompt` asks before each one; `auto` does not. The default is set in `~/.vtx/config.yml` under `permissions.mode`, switched in the TUI with `/permissions`, or cycled with `Alt+Ctrl+P`. Known-destructive shell commands are blocked in both modes unless the session explicitly authorizes them. See [docs/permissions.md](docs/permissions.md).

## The codemode sandbox

A `codemode` script is ordinary Python executed by a separate worker process as the user who launched `vtx`. It gets full builtins, the full standard library, installed packages, the filesystem, subprocesses, and the network. There is no interpreter-level confinement, and none is attempted: restricting the interpreter would cost the model every library it might have used while stopping short of what actually matters, which is the host's own state.

That state is out of reach because it lives in another process. The worker imports nothing from `vtx` except through the tool call channel, so the running agent, the session, and every registered tool are unreachable by direct access. The boundary is load-bearing for one reason: a deadline has to be a `kill`, not a cooperative request. An infinite loop, a pathological regex, a blocking read, and a runaway C extension all terminate the same way.

What a script can reach is governed by per-tool *exposure*, not by the interpreter:

| Exposure | Declared to the model | Callable from a script | Listed in the catalog | Found by `tool_search` |
|----------|----------------------|------------------------|-----------------------|--------------------------|
| `direct` | yes | yes | no | no |
| `codemode` | - | yes | yes | yes |
| `codemode-deferred` | - | yes | - | yes |
| `deferred` | - | yes | - | yes |
| `hidden` | - | - | - | - |

Servers default to `codemode`; `tool_exposure` overrides per tool by exact name or `*` pattern. See [docs/codemode.md](docs/codemode.md) and [docs/mcp.md](docs/mcp.md).

If you need a stronger boundary than "your own permissions", containerize `vtx`.

## Contributing

[AGENTS.md](AGENTS.md) holds the project rules for humans and agents alike.

## Development

```bash
uv run ruff format .
uv run ruff check .
uvx ty check .
uv run --no-sync python -m pytest -p no:cacheprovider path/to/test_file.py  # one file
uv run --no-sync python -m pytest -q -n auto                                # larger runs
```

Run a single file's tests rather than the full suite; it is slow and resource intensive. See [docs/development.md](docs/development.md).

## License

[Apache License 2.0](LICENSE)