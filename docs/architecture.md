# Architecture

Vtx is a minimalist coding-agent harness built around a small, transparent runtime. This page maps the core `src/vtx` package.

## Two backends

`vtx-coding-agent` ships two agentic execution engines:

- **`vtx` native event loop** (`src/vtx/loop.py`, `src/vtx/turn.py`) — the original single-session, event-stream loop. It powers the TUI and the headless CLI (`vtx -p "..."`). Handles thinking streaming, tool permissions, file edits, and compaction between turns.
- **`agenite_claw` advanced backend** (`src/agenite_claw/...`) — a production-grade multi-session gateway loop with concurrent tool batching, context governance, crash-checkpoint restore, mid-turn message injection, subagent orchestration, MCP servers, cron turns, and a hook lifecycle. Powers the `agenite-claw` gateway, WebUI, and 16+ chat-channel integrations. Requires the `[claw]` extra.

## Core runtime (`src/vtx`)

| Module | Responsibility |
|--------|----------------|
| `turn.py` | `run_single_turn` — one agent turn: build messages, call the model, execute tool calls, emit events. |
| `loop.py` | The interactive turn loop: compaction, goal checks, lifecycle events. |
| `events.py` | Typed event stream (`TextDeltaEvent`, `ToolStartEvent`, `ToolResultEvent`, `TurnEndEvent`, …). |
| `core/types.py` | Message and content types (`AssistantMessage`, `ToolCall`, `ToolResult`, `Usage`, …). |
| `tools/` | The 11 built-in tools as Pydantic-validated `BaseTool` subclasses. |
| `prompts/` | System-prompt assembly (`identity.py`, `tooling.py`, `env.py`, `builder.py`). |
| `llm/` | Provider catalog, model fetching, and provider SDK adapters. |
| `session.py` | JSONL session persistence and resumption. |
| `extensions.py` | Extension discovery, the `ExtensionAPI`, and the event bus. |
| `agents/` | Switchable handoff-agent discovery and schema. |
| `goal.py` | Goal-mode manager and evaluator. |
| `config.py` | Configuration schema, loading, and migration. |
| `ui/` | The Textual TUI and `app.py`. |
| `sdk/` | The programmatic multi-agent SDK (see [sdk/README.md](sdk/README.md)). |

## Message flow (one turn)

1. The loop builds the message list (system prompt + history + user input).
2. `run_single_turn` calls the model and streams `TextDeltaEvent`s.
3. On a tool call, the permission gate runs (if `prompt` mode); the tool executes and emits `ToolStartEvent` / `ToolResultEvent`.
4. The tool result is appended and the turn continues until the model stops or a stop reason is hit.
5. Between turns, compaction and goal evaluation may run; lifecycle events fire through the extension bus.

## Context management

- The system prompt is intentionally lean (base + tool guidelines + env block). See [README](../README.md) for token figures.
- `compaction` summarizes old turns when context exceeds `compaction.threshold_percent`.
- `git_context` optionally attaches a `git status`/`git diff` snapshot at startup.

## Sub-agents

The `task` tool (`src/vtx/tools/task.py`) dispatches isolated sub-agent sessions with their own tool surface. Built-in presets (`general-purpose`, `Explore`, `Plan`) are configured under `task.subagent_presets`. See [tools.md](tools.md).
