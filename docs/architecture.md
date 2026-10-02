# Architecture

Vtx is a minimalist coding-agent harness built around a small, transparent runtime. This page maps the ten `src/vtx` packages.

## Package layout

| Package | Responsibility |
|--------|----------------|
| `vtx.protocol` | **Leaf.** The wire vocabulary every layer speaks: message and stream types, the provider/tool contracts, error formatting. Imports nothing from `vtx`. |
| `vtx.telemetry` | **Leaf.** Tracing: spans, processors, console and JSONL exporters. Imports nothing from `vtx`. |
| `vtx.git` | Git and GitHub integration: branch metadata, the `gh` CLI wrapper, GitHub App auth. |
| `vtx.core` | Shared foundations: agent events, the permission gate, notifications, the scratchpad dir, compaction, handoff prompts, paths, the user config schema and its defaults, the theme palette registry, harness knobs, and package-version resolution. |
| `vtx.ai` | LLM layer only: provider catalog, OAuth, SDK adapters, model catalog, rate limits, tool parsing. No harness code. |
| `vtx.codemode` | Confined script execution: the sandbox, its isolation layers, and the discovery/catalog layer (see [codemode.md](codemode.md)). |
| `vtx.agent` | The product-neutral agent harness: loop, turn engine, session store, tool registry, prompt/context assembly, extensions, hooks, goals, and the programmatic SDK. |
| `vtx.mcp` | MCP client: server config, transports (stdio, in-memory, streamable HTTP), tool/resource exposure, OAuth, and the trust prompt. |
| `vtx.tui` | The Textual terminal UI: chat rendering, input, slash commands, session tree selector. |
| `vtx.coding_agent` | The product layer: CLI entry point (`coding_agent.cli:main`), headless runner, the concrete filesystem tools and their default registry, and the built-in skills package. |

### Dependency direction

```
protocol, telemetry          (leaves)
   ↓
core, git                   ai ──→ core
   ↓                          ↓
mcp ───────────────────────→  agent ──→ codemode
   ↓                          ↓
tui ───────────────────────→  coding_agent
```

The harness (`vtx.agent`) never imports `vtx.coding_agent` or `vtx.tui`; product code injects everything engine-side (system-prompt builder, context loader, tool registry, user config knobs). Every remaining upward edge — `agent → mcp`, `agent → tui`, `core → agent`, `codemode → agent` — is a **function-local** import, so no module-level cycle exists and layering stays verifiable. `vtx.core` reaches upward only from `config.py`, which merges user YAML into the harness knobs in `core/harness_config.py` and resolves provider catalogs and subagent limits.

Enforced by `.github/workflows/ci.yml`. The one runtime seam that used to break it — the sub-agent runner, where three call sites re-imported `vtx.coding_agent.tools.task` looking for a divergent `_run_subagent` — is now an explicit `set_subagent_runner()` hook in `vtx.agent.tools.task`. That module re-exported the same function object, so the lookup could never differ; the back-edge existed only to keep old monkeypatches working. `vtx.coding_agent.prompts.rlm` moved to `vtx.agent.prompts.rlm` for the same reason.

The harness used to live at `vtx.ai.agent`, nested inside the LLM package, which made `vtx.ai` a 116-module grab bag holding the entire runtime. It is now a sibling of `vtx.ai`, so `vtx.ai` holds only AI code. Likewise every module that duplicated a harness module has been removed rather than aliased: `coding_agent/{prompts,context,goal}`, `runtime.py`, `version.py`, `config.py`, `defaults/`, `gh_cli.py`, `git_branch.py`, `self_update.py`, `diff_display.py`, and `themes.py` each have a single home. One module path per concern, one implementation to keep correct.

## Two run surfaces

- **TUI** (`vtx`, `tui.launch.run_tui`) — the interactive Textual app.
- **Headless** (`vtx -p "..."`, `coding_agent.headless`) — one prompt in, text out, exit code reflects the stop reason.

Both drive the same `ConversationRuntime` → `Agent` stack.

## Agent harness (`vtx.agent`)

| Module | Responsibility |
|--------|----------------|
| `loop.py` | `Agent.run(query)` — the interactive turn loop: streams events per turn, runs compaction between turns, queues follow-ups and steering. Product-agnostic: system prompt and context are injected. |
| `turn.py` | `run_single_turn` — one turn: stream from the provider, execute tool calls, emit events. Handles retries, empty-response recovery, length recovery, mid-turn injections. |
| `agent_runner.py` | `run_agent_turn(spec)` — thin stateless wrapper over `run_single_turn` used by sub-agents and tests. |
| `runtime.py` | `ConversationRuntime` — composition root wiring provider, tools, extensions, agents; owns model/thinking switches, sessions, compaction and handoff entry points; resolves each model's real context window onto the engine. |
| `session.py` | JSONL session persistence with a branching tree of entries (see [sessions.md](sessions.md)). |
| `dispatcher.py` | Per-task context (`DispatcherContext`) so tools like `delegate_subagent` can reach provider/model/session info. |
| `context_governance.py` | Budgets oversized tool results before they are sent back to the model. |
| `extensions.py`, `extension_manager.py` | Extension discovery, the `ExtensionAPI`, and the event bus (see [extensions.md](extensions.md)). |
| `hooks/` | `.vtx/hooks.yml` declarative hooks and the `AgentHook` protocol (see [extensions.md](extensions.md)). |
| `sdk/` | The programmatic multi-agent SDK (see [sdk/README.md](sdk/README.md)). |
| `tools/` | Tool contract only: `BaseTool` and JSON-schema slimming for LLM tool definitions. Concrete tools live in the coding agent. |
| `background.py` | Background-task notification tag shared between parent and sub-agents. |
| `codemode/` | Confined script execution — the sandbox, its isolation layers, and the discovery/catalog layer (see [codemode.md](codemode.md)). Promoted to its own `vtx.codemode` package; the harness imports it one-way. |

Support modules: `tools_manager.py` (auto-download of `fd`/`rg` into `~/.vtx/bin`). Harness-owned runtime knobs (max turns, compaction policy, idle timeout) live in `core/harness_config.py` with product-neutral defaults, and `core/config.py` mirrors user YAML into them. Package-version resolution lives in `core/version.py`, which every package reads from.

## Coding-agent layer (`vtx.coding_agent`)

| Module | Responsibility |
|--------|----------------|
| `cli.py` | Console-script entry point (`vtx = vtx.coding_agent.cli:main`) and its auth/provider flags. |
| `headless.py` | One prompt in, text out; exit code reflects the stop reason. |
| `tools/` | The built-in `BaseTool` implementations (`bash`, `edit`, `find`, `grep`, `read`, `write`, `skill`) plus the default registry (`DEFAULT_TOOLS`) (see [tools.md](tools.md)). |
| `builtin_skills/` | The skills package registered into the harness at import time. |
| `agents/` | Switchable handoff agents: schema (`AgentDef`), loader, registry (see [agents.md](agents.md)). |

The composition root is `vtx.agent.runtime`, not `coding_agent/runtime.py`; prompt, context, and goal assembly likewise live under `vtx.agent.{prompts,context,goal}`.

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

The `delegate_subagent` tool dispatches isolated sub-agent sessions with their own tool surface, system prompt and JSONL session. `subagent_type` is matched against the agents in `.vtx/agent/` and `~/.vtx/agent/`; anything else runs the default sub-agent (there are no built-in presets). `background: true` runs via `BackgroundTaskManager` and notifies on a later turn.

Completion has two halves, and both are load-bearing. `BackgroundTaskManager` exposes a settlement listener, so a sub-agent that finishes *after* the parent turn ends still reaches a session that is sitting idle — the TUI resumes itself rather than waiting for the user to type. `Agent.run` then drains anything already settled *before the first model call* of that run, not between turns, so the result is in context for the turn that reacts to it. Draining only between turns meant a sub-agent outliving its parent turn was appended to the session and shown to the model one turn too late. A completion is delivered exactly once (`drain_completed` flips `notified`), and a wake-up turn can cascade at most `MAX_BACKGROUND_WAKEUPS` times before the session stops resuming itself and says so.

Every dispatch passes through `agent.subagents.SubagentScheduler`, a FIFO admission queue capped by `task.max_concurrent` (default 4, `0` = uncapped). A sub-agent over the cap waits for a slot *before* it builds a session or a provider, so "running" and "queued" are real counts.

Both counters, plus a live row per sub-agent (name, description, turns/tool/token counters, current activity), render in the pinned **Agents** panel (`tui/agents_panel.py`) above the editor, fed from the process-wide `tui/goal_agents.REGISTRY`. The registry keys runs by tool-call id, so four concurrent `Explore` agents are four rows. The goal beacon renders the same rows from the same registry while a goal is focused; the chat log keeps only a static dispatch receipt per call.

## Compaction

`core.compaction.is_overflow()` compares context tokens against the active model's context window at `threshold_percent` (default 80%). The window comes from the model catalog entry for the selected provider/model; if the model is unknown there, it falls back to `agent.default_context_window`. Stale provider labels on resumed sessions are healed at startup so lookups target the right catalog entry. A summarization prompt asks the model for a handoff summary; the session records a `compaction` entry keeping the tail of the branch intact.
