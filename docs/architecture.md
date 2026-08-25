# Architecture

Vtx is a minimalist coding-agent harness built around a small, transparent runtime. This page maps the four `src/` packages.

## Package layout

| Package | Responsibility |
|--------|----------------|
| `src/core` | Zero-dependency foundations: message/event types, permission gate, compaction, handoff prompts, tracing, paths, notifications. |
| `src/ai` | LLM layer: provider catalog, OAuth, SDK adapters, plus the agent harness under `ai.agent` (loop, turn engine, session store, tools, prompts, context, extensions, agents, hooks). |
| `src/coding_agent` | Application shell: CLI entry point (`coding_agent.cli:main`), config schema and migrations, headless runner, themes. |
| `src/tui` | The Textual terminal UI: chat rendering, input, slash commands, session tree selector. |

Dependency direction: `core` → (nothing), `ai` → `core`, `coding_agent` → `ai` + `core`, `tui` → all three.

## Two run surfaces

- **TUI** (`vtx`, `tui.launch.run_tui`) — the interactive Textual app.
- **Headless** (`vtx -p "..."`, `coding_agent.headless`) — one prompt in, text out, exit code reflects the stop reason.

Both drive the same `ConversationRuntime` → `Agent` stack.

## Core runtime (`src/ai/agent`)

| Module | Responsibility |
|--------|----------------|
| `runtime.py` | `ConversationRuntime` — wires provider, tools, extensions, agents; owns model/thinking switches, sessions, compaction and handoff entry points. |
| `loop.py` | `Agent.run(query)` — the interactive turn loop: streams events per turn, runs compaction between turns, queues follow-ups and steering. |
| `turn.py` | `run_single_turn` — one turn: stream from the provider, execute tool calls, emit events. Handles retries, empty-response recovery, length recovery, mid-turn injections. |
| `agent_runner.py` | `run_agent_turn(spec)` — thin stateless wrapper over `run_single_turn` used by sub-agents and tests. |
| `session.py` | JSONL session persistence with a branching tree of entries (see [sessions.md](sessions.md)). |
| `dispatcher.py` | Per-task context (`DispatcherContext`) so tools like `task` can reach provider/model/session info. |
| `tools/` | Built-in tools as Pydantic-validated `BaseTool` subclasses (see [tools.md](tools.md)). |
| `prompts/` | System-prompt assembly: `identity.py` (base prompt sections), `tooling.py` (per-tool guidelines), `env.py` (env block), `builder.py`. |
| `context/` | `AGENTS.md`/`CLAUDE.md` discovery, skills loading, git snapshot. |
| `context_governance.py` | Budgets oversized tool results before they are sent back to the model. |
| `extensions.py` | Extension discovery, the `ExtensionAPI`, and the event bus (see [extensions.md](extensions.md)). |
| `agents/` | Switchable handoff agents: schema (`AgentDef`), discovery, loader, registry (see [agents.md](agents.md)). |
| `hooks/` | `.vtx/hooks.yml` declarative hooks and the `AgentHook` protocol (see [extensions.md](extensions.md)). |
| `sdk/` | The programmatic multi-agent SDK (see [sdk/README.md](sdk/README.md)). |

Support modules: `background.py` (sub-agent task manager), `tools_manager.py` (auto-download of `fd`/`rg` into `~/.vtx/bin`), `gh_cli.py` (PR autocomplete), `git_branch.py`, `diff_display.py`.

## LLM layer (`src/ai`)

| Module | Responsibility |
|--------|----------------|
| `base.py` | `BaseProvider.stream()` returns an `LLMStream` of `StreamPart`s; `ProviderConfig`; env-var API key map; local-endpoint detection. |
| `provider.yaml` / `provider_catalog.py` | 57 built-in providers plus user YAML overrides from `~/.vtx/providers/*.yaml`. |
| `providers/openai_sdk.py`, `anthropic_sdk.py` | Streaming adapters over the official SDKs; `mock.py` for offline runs/tests; `sanitize.py` cleans provider payloads. |
| `oauth/` | Device/code login flows for GitHub Copilot, OpenAI (Codex), Cline (WorkOS), and dynamic providers. |
| `dynamic_models.py` | Live model catalogs fetched from provider endpoints / models.dev, cached ~6 h under `~/.vtx/models/`. |
| `context_length.py` | Context-window/output limits per model (models.dev, cached 24 h). |
| `rate_limit.py` | Retry/backoff behaviour shared by adapters. |
| `tool_parser.py` | Extracts tool calls embedded in plain-text responses (for models that emit XML-style calls). |

Api types: `openai-sdk` (chat completions), `openai-responses`, `anthropic`.

## Message flow (one turn)

1. `ConversationRuntime.initialize()` resolves provider/auth, loads context (AGENTS.md, skills) and creates or resumes a `Session`.
2. `resolve_system_prompt()` composes: base identity → tool guidelines → `<project_guidelines>` → skills index → optional git snapshot → env block.
3. `Agent.run()` yields events; each turn calls `run_single_turn`.
4. `_TurnRunner` streams `StreamPart`s (thinking/text/tool-call deltas) and emits typed events (`ThinkingDeltaEvent`, `TextDeltaEvent`, `ToolStartEvent`, …).
5. On a tool call: permission gate runs (see [permissions.md](permissions.md)); mutating tools wait for approval in `prompt` mode. The tool executes and emits `ToolStartEvent`/`ToolResultEvent`; extension handlers may rewrite or block.
6. Tool results pass through context governance, are appended to the session, and the loop continues until the model stops or a stop reason fires (`stop`, `length`, `tool_use`, `error`, `interrupted`, `steer`).
7. Between turns: compaction if usage crosses `compaction.threshold_percent`, queued follow-ups/steers drain in.

## Sub-agents

The `task` tool dispatches isolated sub-agent sessions with their own tool surface, system prompt and JSONL session. Presets (`general-purpose`, `Explore`, `Plan`) come from `task.subagent_presets`; a user-defined agent of the same name wins. `background: true` runs via `BackgroundTaskManager` and notifies on a later turn.

## Compaction

`core.compaction.is_overflow()` compares context tokens against the window at `threshold_percent`. A summarization prompt asks the model for a handoff summary; the session records a `compaction` entry keeping the tail of the branch intact.
