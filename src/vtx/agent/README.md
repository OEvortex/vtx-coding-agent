# `vtx.agent`

The product-neutral agent harness: the turn loop, the composition root, session persistence, the tool contract, prompt and context assembly, extensions, hooks, goals, and the programmatic SDK.

This package used to live at `vtx.ai.agent`, which made `vtx.ai` a 116-module grab bag holding the whole runtime. It is now a top-level sibling of `vtx.ai`, so `vtx.ai` contains only LLM-provider code.

## What it owns

- **The engine.** `loop.py` (`Agent.run`) drives turns; `turn.py` (`run_single_turn`) executes one request/response cycle with streaming, tool execution, permission checks, retries and cancellation; `agent_runner.py` is a thin stateless wrapper over a turn.
- **The composition root.** `runtime.py` (`ConversationRuntime`) owns provider construction, tool activation, extension and agent wiring, model/thinking switches, sessions, compaction and handoff entry points. Both run surfaces (TUI and headless) drive this same object.
- **The tool contract.** `tools/base.py` (`BaseTool`) plus JSON-schema slimming for LLM tool definitions, a global tool registry, and the harness-native tools: `ask_user`, `delegate_subagent`, `web`, `goal`, `codemode`, `tool_search`.
- **Prompt and context assembly.** `prompts/` (section constants and `build_system_prompt`) and `context/` (AGENTS.md discovery, skills catalog, git snapshot).
- **Extension surface.** `extensions.py` (discovery, `ExtensionAPI`, `EventBus`), `extension_manager.py` (install from PyPI/GitHub), `hooks/` (declarative `.vtx/hooks.yml` plus the in-process `AgentHook` protocol).
- **Session state.** `session.py` (append-only JSONL with a branching entry tree), `snapshot.py` and `revert.py` (turn-level undo/redo over that tree plus the working directory), `background.py` (background sub-agent lifecycle and completion delivery).
- **Sub-agents.** `subagents.py` (concurrency admission) and `dispatcher.py` (`DispatcherContext`, the single parent-state slot dispatching tools read).
- **Goals.** `goal/` (session-scoped durable objectives with an independent completion auditor).
- **The SDK.** `sdk/` — a programmatic multi-agent API over the same runtime.

## What it does not own

- **No UI.** `vtx.agent` does not import `vtx.tui` or `vtx.coding_agent` at module level. The two exceptions are function-local imports of *rendering* helpers only, and neither creates an import cycle: `tools/task.py:508` imports `vtx.tui.blocks.TaskToolBlock` inside `ui_block` (the property that names a widget class), and `tools/web.py:20` imports `vtx.tui.tool_output` escaping helpers inside a formatter.
- **No product configuration.** There is no `config.py` in this package. Harness tunables live in `vtx/core/harness_config.py` (`HarnessConfig`: max turns, context-window defaults, compaction and idle policy) with product-neutral defaults; the user-facing YAML schema and loader are `vtx/core/config.py`, which mirrors user config into the harness object via `apply_harness_settings`.
- **No concrete filesystem tools.** `read`, `write`, `edit`, `bash`, `find`, `skill` live in `vtx/coding_agent/tools/`. They register themselves into the harness registry at import time, which is why an importing process sees a wider default tool set than a bare `import vtx.agent.tools`.
- **No model catalog, provider adapters, or sandbox.** Those are `vtx.ai` and `vtx.codemode`.

## Dependency direction

```
protocol, telemetry, git          (leaves)
   ↓
core, ai ─────────────────────────→  agent ──→  codemode
   ↓                                 ↓
mcp ──────────────────────────────→   ↓
tui ──────────────────────────────→  coding_agent
```

Imports **into** `vtx.agent`: `vtx.protocol`, `vtx.ai`, `vtx.core`, `vtx.codemode` (harness-internal use), plus function-local `vtx.mcp` and `vtx.tui`. Imports **out of** `vtx.agent`: `vtx.coding_agent` (34 modules: CLI, headless runner, product tools, its own `agents/` fork, TUI commands) and `vtx/__init__.py`, which re-exports the SDK surface.

Two upward edges deserve naming because they look like violations but are not: `vtx.core.config` calls `vtx.agent.subagents.set_limit` to resize the scheduler on config reload, and `vtx.codemode` plus `vtx.mcp` are imported inside functions. Layering is enforced in CI.

## Module map

| Module | Responsibility |
|--------|----------------|
| `runtime.py` | `ConversationRuntime`, the composition root. |
| `loop.py` | `Agent.run` — the turn loop; compaction between turns, follow-up and steer queues, background-task draining. |
| `turn.py` | `run_single_turn` — one LLM cycle: streaming, tool calls, approvals, retries, cancellation. |
| `agent_runner.py` | `run_agent_turn(spec)` — provider-agnostic single-turn wrapper. |
| `session.py` | JSONL session store with a branching entry tree. |
| `dispatcher.py` | `DispatcherContext` / `get_context()` / `set_context()` — the parent-state slot sub-agent tools read. |
| `subagents.py` | `SubagentScheduler`, the FIFO admission queue; `get_scheduler()`, `set_limit()`, `reset_scheduler()`. |
| `context_governance.py` | `prepare_for_model(messages)` — drops orphaned tool results and budgets oversized ones before each model call. Pure function, no I/O. |
| `tools/` | `BaseTool`, the registry (`register_tool`, `get_all_tools`, `get_default_tools`, `get_parent_only_tools`, `get_tool_definitions`), schema-to-pydantic (`schema.py`), and the harness-native tools. |
| `prompts/` | `build_system_prompt(...)` plus the section constants (`identity`, `tooling`, `env`, `ponytail`). |
| `context/` | `Context`, AGENTS.md loading, skills discovery/catalog, git context, XML escaping. |
| `extensions.py`, `extension_manager.py` | Extension discovery and `ExtensionAPI`/`EventBus`; install/list/uninstall from PyPI or GitHub. |
| `hooks/` | Declarative hooks (`loader.py`, `registry.py`, `types.py`) and the `AgentHook` protocol; `bridge.py` maps extension events onto hook events. |
| `agents/` | Switchable handoff agents from `.vtx/agent/<name>.py`: `AgentDef` schema, loader, `AgentRegistry`, active tool/command composition. |
| `goal/` | `GoalService` (the sole mutation boundary), markdown+JSONL storage, the `goal` tool, and the completion auditor. |
| `background.py` | `BackgroundTaskManager`, record persistence, `drain_completed()`, the notification tag. |
| `snapshot.py`, `revert.py` | Content-addressed working-tree snapshots in a shadow git repo; turn-level undo/redo. |
| `skills.py`, `skills_refresh.py` | Skills re-export surface; the refreshable catalog slot that swaps in place when the skill set changes. |
| `tools_manager.py` | Downloads `fd`/`rg` binaries into `~/.vtx/bin`. |
| `async_utils.py` | `await_or_cancel`, `cancel_and_await`. |
| `xml.py`, `context/_xml.py` | Shared XML escaping helpers. |
| `sdk/` | The programmatic SDK (below). |

## The programmatic SDK

`vtx.agent.sdk` exports 55 public names: `Agent`, `Runner`, `RunConfig`, `RunResult`, the `@tool` decorator and `FunctionTool`, sessions (`Session` protocol, `InMemorySession`, `JSONLSession`, `SessionSettings`), handoffs, guardrails, permission policies, approval state, run items, and tracing re-exports.

Offline-runnable quick start (verified against this tree; `MockProvider` comes from `vtx.ai.providers.mock`):

```python
import asyncio

from vtx.agent.sdk import Agent, Runner, tool
from vtx.ai.providers.mock import MockProvider


@tool
def get_weather(city: str) -> str:
    """Return the current weather for a city."""
    return f"Sunny in {city}"


agent = Agent(
    name="Weather bot",
    instructions="Be concise.",
    model="gpt-4o-mini",
    provider=MockProvider(scenario="simple_text"),
    tools=[get_weather],
)

result = asyncio.run(Runner.run(agent, "Weather in Tokyo?"))
print(result.stop_reason)        # stop
print(result.final_output)       # the assistant's text
```

`Runner.run`, `Runner.run_sync` and `Runner.run_streamed` share one signature: `(starting_agent, input, *, session=None, run_config=None, max_turns=None, cancellation=None, context=None)`. `RunResult` carries `final_output`, `new_items`, `interruptions`, `state`, `stop_reason` and `usage`.

`Agent` is a dataclass: `name`, `instructions` (string or callable), `model`, `provider` (a `BaseProvider`, a dict, or `None` for env/config fallback), `tools`, `handoffs`, `output_type`, `input_guardrails`, `output_guardrails`, `tool_use_behavior`, `metadata`, `needs_approval_tools`. `agent.clone(**overrides)` copies with overrides.

Drop `provider=MockProvider(...)` and set `provider={"name": "openai"}` for a real run; the provider dict's recognised keys are `name`, `sdk`, `api_key`, `base_url`, `model`, `max_tokens`, `temperature`, `thinking_level`, `default_headers`.

`docs/sdk/` covers the SDK in depth: `runner.md`, `agents.md`, `tools.md`, `multi_agent.md`, `approvals.md`, `permissions.md`, `guardrails.md`, `sessions.md`, `skills.md`, `tracing.md`.

## Sub-agents

`delegate_subagent` (`tools/task.py`, class `TaskTool`) is the only dispatch path. It resolves `subagent_type` against user-defined agents in `.vtx/agent/` and `~/.vtx/agent/`; there are **no built-in sub-agent presets**. An empty or unknown name runs a default sub-agent with the parent's tool surface minus the parent-only tools, capped at `DEFAULT_MAX_TURNS = 200`. Results are truncated to `MAX_RESULT_CHARS = 32_000`; transcripts are shown up to `MAX_TRANSCRIPT_LINES = 200`.

Every dispatch goes through `SubagentScheduler` (`subagents.py`), a FIFO admission queue:

- The limit comes from `task.max_concurrent` in `vtx.core.config` (default `4`; `0` means uncapped). `SubagentScheduler.DEFAULT_MAX_CONCURRENT` is the fallback when config cannot be read.
- **A sub-agent over the cap waits for its slot before it builds a session or a provider.** Waiting happens in `_run_subagent` before `_run_admitted_subagent`, so `running` and `queued` are real counts, not estimates.
- `set_limit()` resizes after a config reload without pre-empting anything in flight; it only holds new arrivals until the in-flight count drops below the new limit. `reset_scheduler()` drops all queue state.
- The scheduler is deliberately dumb: one asyncio loop, one queue, no priorities, no cross-process state.

`subagent_type` resolution, session creation, provider derivation, the run loop and progress events are all internal to `_run_admitted_subagent`. Products override the runner with the explicit hook:

```python
from vtx.agent.tools.task import set_subagent_runner, resolve_subagent_runner
```

`set_subagent_runner(fn)` installs a replacement; `resolve_subagent_runner()` returns it or the built-in `_run_subagent`. This replaced three call sites that re-imported `vtx.agent.tools.task` looking for a divergent `_run_subagent` — that module re-exports the same function object, so the lookup could never have differed, and the back-edge to the product layer existed only to keep old monkeypatches alive.

`background: true` schedules through `BackgroundTaskManager` instead and returns a `task_id` immediately. Cancellation differs by design: a background task is not cancelled by the parent's `cancel_event` (its call already returned) and is only stopped explicitly or by `ConversationRuntime.close()`.

Sub-agents auto-approve their own approval prompts and answer `ask_user` with an empty response — they have no way to reach the user's terminal.

## Goals

Goals are **session-scoped**. `GoalService` is bound to one vtx session; a goal created by session A is invisible to every other instance running against the same project, so parallel sessions never fight over focus or mutate each other's work.

- Storage is markdown goal files plus an append-only JSONL ledger under `.vtx/goals/` (`storage.py`); the service is the sole mutation boundary and mutates clones, so a failed write commits nothing.
- Each record carries its owning `session_id`. Ownership is backed by a liveness lease under `.vtx/goals/leases/<session_id>.json`, renewed on every `pool()` read with a 120-second TTL. `claimable()` excludes goals whose owner still holds a lease, so **a running instance never offers another instance's goal away**.
- Goals left behind by a dead session surface as orphans: the `goal` tool exposes `list_orphans` and `claim`, and the TUI announces claimable goals at startup without claiming or focusing anything.
- Before archiving, `goal/auditor.py` dispatches an independent read-only sub-agent to re-check the objective, task evidence and verification contract against the real workspace, ending in `<approved/>` or `<disapproved/>`.

## Known wart: the duplicated agent-profile package

`vtx/coding_agent/agents/{api,loader,registry,schema}.py` is a **divergent fork** of `vtx/agent/agents/`. All four files differ from their `vtx.agent` counterparts.

Production uses the `vtx.agent` copy: `runtime.py` imports `AgentRegistry` and `LoadedAgent` from there, and `tools/task.py` resolves sub-agent profiles against it. The fork is used by `coding_agent/cli.py` and by six test modules (`tests/test_agent_profiles.py`, `tests/test_agent_profiles_runtime.py`, `tests/test_agent_profiles_tui.py`, `tests/tools/test_task.py`, `tests/test_extension_manager.py`, `tests/ui/test_custom_tool_blocks.py`).

The divergence is not cosmetic. The fork's `AgentDef` declares `tools`, `skills`, `tool_groups` and `active_tool_group` a second time, after a first declaration of the same four fields — so the two copies parse the same keys but through different code paths. The fork's `AgentRegistry` also lacks `on_change`, `set_active_tool_group` and `switch`, all of which exist on the `vtx.agent` copy and are used by `ConversationRuntime.cycle_active_tool_group`. Both copies end up with the same 24 Pydantic fields.

Reconciling is a behaviour decision, not a move: someone has to decide whether the duplicate declarations are load-bearing for any profile format in the wild, then delete the loser and repoint the six test modules. Until that is decided, editing `vtx/agent/agents/` alone will silently not change what the CLI sees.