# Changelog

All notable changes to ctx-sdk are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-24

### Added

- **Initial release** — ctx-sdk is the core agent harness extracted from the monolithic `vtx-coding-agent` package. It provides the agent loop, sessions, tools, plugin composition, MCP client, 65 LLM providers, and a Python SDK.
- **`ctx.protocol`** — leaf package: message, stream, and tool-contract types. Imports nothing from `ctx`.
- **`ctx.telemetry`** — leaf package: spans, processors, console and JSONL exporters.
- **`ctx.git`** — Git and GitHub integration: `gh` CLI wrapper, GitHub App auth.
- **`ctx.core`** — Agent events, permission gate, compaction, config schema and paths, theme registry.
- **`ctx.ai`** — The LLM layer: provider catalog, OAuth, SDK adapters, model catalog, tool parsing.
- **`ctx.agent`** — Product-neutral harness: loop, turn engine, sessions, tool registry, prompts, hooks, goals, SDK.
- **`ctx.mcp`** — Dependency-free MCP client with stdio, Streamable HTTP, and in-memory transports; OAuth sign-in for remote servers.
- **`ctx.codemode`** — Confined code-mode sandbox with enforced budgets (`max_tool_calls`, `max_output_tokens`, `memory_limit_bytes`).
- **`ctx.term`** (partial) — Product-neutral terminal UI primitives shared with ctx-term via `pkgutil.extend_path`.
- **Optional dependencies** — `[term]` for terminal UI building blocks, `[gh-app]` for GitHub App authentication.

### Changed

- **Monorepo split** — the harness was extracted from `vtx-coding-agent` and reorganized into ten focused packages under `ctx`. The dependency direction is `protocol`/`telemetry` → `core`, `git`, `ai` → `agent` → product packages.
- **One module path per concern** — all re-export shims and duplicate module paths were removed. Every name has a single home.
- **Lazy barrel imports** — `__init__.py` files now resolve lazily through PEP 562 `__getattr__`, cutting startup time roughly in half.
