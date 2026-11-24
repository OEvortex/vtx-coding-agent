# ctx-agent

**A coding agent that lives in your terminal.**

[![PyPI version](https://img.shields.io/pypi/v/ctx-agent.svg)](https://pypi.org/project/ctx-agent/)
[![Python](https://img.shields.io/pypi/pyversions/ctx-agent.svg)](https://pypi.org/project/ctx-agent/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

`ctx-agent` is the coding agent product built on the [ctx-sdk](https://pypi.org/project/ctx-sdk/) harness. It provides a keyboard-driven TUI, a headless CLI for scripts and CI, built-in tools, and the `ctx` console script.

## Quick start

```bash
pip install ctx-agent

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

## Features

- **Interactive TUI** — keyboard-driven terminal interface with session management
- **Headless CLI** — run tasks non-interactively for scripts and CI pipelines
- **Built-in tools** — `read`, `apply_patch`, `bash`, `grep`, `skill`, `todo`, `web`, `ask_user`, `codemode`, `subagent`
- **65 LLM providers** — supports Anthropic, OpenAI, and 60+ more via provider catalog
- **Durable sessions** — resume, fork, and revert turns with snapshots
- **Plugin system** — every subsystem is replaceable via plugin composition
- **MCP client** — connect to Model Context Protocol servers
- **Self-extending** — ships a skill that teaches the model how to add new capabilities

## CLI commands

```bash
ctx                                    # interactive TUI
ctx -p "explain this repo"             # one prompt, then exit
ctx -p - < prompt.md                   # prompt from stdin
ctx --continue                         # resume the most recent session
ctx --resume 4f2a                      # resume a specific session
ctx acp                                # Agent Client Protocol server
ctx update                             # self-update to latest PyPI release
ctx auth status                        # check authentication status
ctx models list                        # list available models
ctx config list|get|set                # manage configuration
ctx plugin ...                         # manage plugins
```

## Documentation

Full reference at [github.com/OEvortex/ctx-coding-agent](https://github.com/OEvortex/ctx-coding-agent).

## License

[Apache License 2.0](LICENSE)
