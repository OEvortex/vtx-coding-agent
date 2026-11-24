# Changelog

All notable changes to ctx-agent are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-24

### Added

- **Initial release** — ctx-agent is the coding agent product built on the [ctx-sdk](https://pypi.org/project/ctx-sdk/) harness. It provides a keyboard-driven TUI, a headless CLI for scripts and CI, built-in tools, and the `ctx` console script.
- **Interactive TUI** — keyboard-driven terminal interface with session management, model picker, and slash commands.
- **Headless CLI** — run tasks non-interactively: `ctx -p "add tests for ctx.git"`.
- **Built-in tools** — `read`, `apply_patch`, `bash`, `grep`, `find`, `skill`, `todo`, `web`, `ask_user`, `codemode`, `subagent`.
- **65 LLM providers** — supports Anthropic, OpenAI, and 60+ more via provider catalog.
- **Durable sessions** — resume, fork, and revert turns with snapshots.
- **Plugin system** — every subsystem is replaceable via plugin composition.
- **MCP client** — connect to Model Context Protocol servers.
- **Self-extending skills** — ships a skill that teaches the model how to add new capabilities.
- **`ctx.term` product layer** — app shell, chat pane, panels, dialogs, slash commands, and tool-specific blocks that join the base toolkit from ctx-term via `pkgutil.extend_path`.
- **Codemode sandbox** — the model writes Python scripts that compose other tools, with enforced budgets and governed tool calls.
- **Goal system** — durable, file-backed objectives with task trees, verification contracts, and an auditor sub-agent.
- **`/reload`** — hot reload extensions, agents, skills, and config without losing the conversation.
- **`/undo` and `/redo`** — turn-level rewind with content-addressed working-tree snapshots.
- **Session export** — export sessions as HTML with codemode cells, tool blocks, and usage stats.

### Changed

- **Monorepo split** — the coding agent was extracted from the monolithic `vtx-coding-agent` package. The `ctx` console script replaces `vtx`.
- **Tool rows as plugins** — built-in tools are now plugin rows, making the tool set declarative and replaceable.
- **Compaction preserves far more detail** — the summarization prompt now mandates twelve sections capturing user messages, code, decisions, file state, and completed work.
