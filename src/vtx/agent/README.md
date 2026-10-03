# `vtx.agent`

The product-neutral agent harness behind vtx. It owns the turn loop, the composition root, the `BaseTool` contract, prompt and context assembly, sessions, sub-agent dispatch, extensions and hooks, goals, and a programmatic SDK. It never imports `vtx.coding_agent` or `vtx.tui` at module level, so both the TUI and the headless runner drive the same `ConversationRuntime` without the harness depending on either. This package used to be nested at `vtx.ai.agent`, which made `vtx.ai` a 116-module grab bag holding the whole runtime; it is now a top-level sibling of `vtx.ai`, which now contains only provider code.

## Usage

The SDK is the shortest way in. Build an `Agent`, give it a tool, run it:

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

print(result.stop_reason)   # stop
print(result.final_output)  # the assistant's final text
print(result.usage)         # aggregated tokens across every turn
```

`MockProvider` (`vtx.ai.providers.mock`) replays scripted streams, so this runs offline. Drop it and set a provider dict for a real call - the recognised keys are `name`, `sdk`, `api_key`, `base_url`, `model`, `max_tokens`, `temperature`, `thinking_level`, `default_headers`:

```python
agent = Agent(name="Ops", model="gpt-4o", provider={"name": "openai"}, tools=[deploy])
```

`Runner.run`, `Runner.run_sync` and `Runner.run_streamed` share one signature: `(starting_agent, input, *, session=None, run_config=None, max_turns=None, cancellation=None, context=None)`.

- **`Agent`** is a dataclass: `name`, `instructions` (a string or a callable), `model`, `provider` (a `BaseProvider`, a dict, or `None` for env/config fallback), `tools`, `handoffs`, `output_type`, `input_guardrails`, `output_guardrails`, `tool_use_behavior`, `metadata`, `needs_approval_tools`. `agent.clone(**overrides)` copies it with overrides.
- **`RunResult`** carries `final_output`, `new_items`, `interruptions`, `state`, `stop_reason`, `usage`, `agent_name`. `to_input_list()` turns the run back into input items you can feed to a later `Runner.run`.
- **`@tool`** turns a typed Python function into a `BaseTool`. The docstring becomes the model-facing description. Keyword arguments: `name`, `description`, `needs_approval=True`, `mutating=False` (read-only, so no permission prompt), `input_guardrails`, `output_guardrails`, `tool_icon`.
- **`Runner.run_streamed`** returns an async iterable of events; `.result` is only valid after the iterator is exhausted, and raises `RuntimeError` if read earlier.

```python
stream = Runner.run_streamed(agent, "Weather in Tokyo?")
async for event in stream:
    print(type(event).__name__)
print(stream.result.final_output)
```

- **`handoff(agent, *, tool_name_override, tool_description_override, on_handoff, input_filter, input_type)`** turns another `Agent` into a transfer tool on the caller. Give the caller `handoffs=[handoff(triage, tool_name_override="escalate")]` and the model can hand the conversation over; `on_handoff` runs at the moment of transfer, `input_filter` rewrites what the new agent sees.
- **`JSONLSession(path=None, *, session_id=None)`** persists a run to the same append-only format the TUI and headless runner use, so a session started from the SDK can be resumed in the TUI and the other way round. Without a `path` it is in-memory only.
- **`InMemorySession(session_id=None)`** keeps items for the life of the process.
- **`SessionSettings(limit=...)`** caps how much history a session replays into a run.
- **`RunConfig`** is the per-run knob bag: `max_turns` (default 50), `permission_policy`, `session_settings`, `tracing_disabled`, `trace_include_sensitive_data`, `nest_handoff_history`, `session_input_callback`, `custom`.

`docs/sdk/` covers the SDK in depth: `runner.md`, `agents.md`, `tools.md`, `multi_agent.md`, `approvals.md`, `permissions.md`, `guardrails.md`, `sessions.md`, `skills.md`, `tracing.md`.

## Approvals and permissions

Two mechanisms, deliberately different. A `PermissionPolicy` decides synchronously per call; an approval interruption stops the run and hands the decision to your code.

```python
from vtx.agent.sdk import Agent, Runner, RunConfig, AllowlistApprove

result = await Runner.run(
    agent,
    "Ship v2",
    run_config=RunConfig(permission_policy=AllowlistApprove(["read", "grep"])),
)
```

- **`PromptApprove`** is the default and mirrors `vtx.permissions.check_permission`: read-only tools and safe bash are auto-approved, mutating tools prompt.
- **`AutoApprove`** allows everything, mirroring `permissions.mode=auto`.
- **`AllowlistApprove(allowlist)`** allows by tool name and prompts for the rest.
- **`PermissionPolicy`** is the base class: implement `decide(tool, arguments)` returning `PermissionDecision.ALLOW` or `.PROMPT`.

For a UI, mark the tools that must pause with `needs_approval_tools={"deploy"}` on the agent. The run ends with `stop_reason="tool_use"`, `result.interruptions` holding `ToolApprovalItem`s, and a `result.state` you can resume from:

```python
result = await Runner.run(agent, "Ship v2")
if result.interruptions:
    state = result.state
    for item in result.interruptions:
        print(item.tool_name, item.arguments)
        state.approve(item)      # or state.reject(item)
    result = await Runner.run(agent, state.original_input)
```

A rejected call returns a "denied by user" tool result so the model can pick another path. `RunState.decision_for(call_id)` tells you what was decided. `ToolApprovalItem.to_input_item()` raises by design - filter approvals out before sending an input list back to the model.

## Composition root

`ConversationRuntime` (`runtime.py`) is the object both run surfaces drive. It takes a cwd, a model selection, an explicit tool list and optional agent-registry and event-bus wiring, and it owns provider construction, tool activation, model and thinking-level switches, compaction, handoff, sessions, and the background-task manager.

`DispatcherContext` (`dispatcher.py`) is the single parent-state slot that dispatching tools read: provider, model, `thinking_level`, `agent_registry`, `cwd`, `system_prompt`, and the product-installed `progress_callback`. `ConversationRuntime` repopulates it on initialise, agent change, model change and thinking-level change. A process with no runtime has no dispatcher context, which is why background sub-agent dispatch is unavailable in a bare headless test harness.

## The turn loop

Three modules, in order:

- `turn.py` (`run_single_turn`) is one request/response cycle: streaming, tool calls, permission checks, retries, cancellation.
- `agent_runner.py` (`run_agent_turn`) is a provider-agnostic single-turn wrapper around it.
- `loop.py` (`Agent.run`) is the outer loop: compaction between turns, follow-up and steer queues, background-task draining.

`context_governance.py` (`prepare_for_model(messages)`) sits in front of every model call. It is a pure function with no I/O: it drops `ToolResultMessage` entries whose `tool_call_id` no preceding assistant tool call matches (orphans from cancelled turns) and truncates any tool result over the per-result budget, replacing the body with a summary so the model keeps a pointer without carrying all of it.

`prompts/builder.py` (`build_system_prompt(cwd, context, tools, *, base_content, include_git_context, include_ponytail, extra_instructions, extra_instructions_mode, skills)`) assembles the prompt from the section modules `identity`, `tooling`, `env` and `ponytail`. `context/` holds the pieces it reads: AGENTS.md discovery, the skills catalog, and the git snapshot.

## Tools

`tools/base.py` (`BaseTool`) is the contract; `tools/__init__.py` is the global registry (`register_tool`, `register_tools`, `unregister_tool`, `get_tool`, `get_all_tools`, `get_default_tools`, `get_parent_only_tools`, `get_tool_definitions`) plus the JSON-schema slimming that keeps LLM tool definitions small. `tools/schema.py` converts JSON schema to Pydantic for argument validation.

The harness-native tools live here: `ask_user`, `delegate_subagent`, `web`, `goal`, `codemode`, `tool_search`. **The concrete filesystem tools do not.** `bash`, `edit`, `read`, `write`, `find`, `grep` and `skill` live in `vtx/coding_agent/tools/` and register themselves into the registry at import time, which is why a process that imports the coding agent sees a wider default tool set than a bare `import vtx.agent.tools`.

`get_parent_only_tools()` returns `{"ask_user", "goal", "delegate_subagent"}`. Sub-agents inherit the parent's tool surface minus these, so a sub-agent cannot recursively spawn one.

## Sub-agents

`delegate_subagent` (`tools/task.py`, class `TaskTool`) is the only dispatch path. It was renamed from `task` because that name collided with the goal system's task list - a model told to "check the task" could mean the todo list or the dispatcher.

There are **no built-in sub-agent presets**. `subagent_type` resolves against user-defined agents in `.vtx/agent/` and `~/.vtx/agent/`; an empty or unknown name runs a default sub-agent with the parent's tool surface minus the parent-only tools, capped at `DEFAULT_MAX_TURNS = 200`. Results are truncated to `MAX_RESULT_CHARS = 32_000`, and transcripts are shown up to `MAX_TRANSCRIPT_LINES = 200` with a "... (n more)" tail.

Every dispatch goes through `SubagentScheduler` (`subagents.py`), a FIFO admission queue:

- The limit comes from `task.max_concurrent` in `vtx.core.config`, default `4`; `0` means uncapped. `SubagentScheduler.DEFAULT_MAX_CONCURRENT` is the fallback when config cannot be read.
- **A sub-agent over the cap waits for its slot before it builds a session or a provider.** Waiting happens in `_run_subagent` before `_run_admitted_subagent`, so `running` and `queued` are real counts the UI can render, not estimates.
- `set_limit()` resizes after a config reload without pre-empting anything in flight; it holds new arrivals only until the running count drops below the new limit. `reset_scheduler()` drops all queue state.
- The scheduler is deliberately dumb: one asyncio loop, one queue, no priorities, no cross-process state.

`subagent_type` resolution, session creation, provider derivation, the run loop and progress events are all internal to `_run_admitted_subagent`. Products override the runner through an explicit hook:

```python
from vtx.agent.tools.task import set_subagent_runner, resolve_subagent_runner

set_subagent_runner(my_runner)      # installs a replacement
resolve_subagent_runner()           # returns it, or the built-in _run_subagent
```

That hook replaced three call sites that re-imported `vtx.agent.tools.task` looking for a divergent `_run_subagent`. The module re-exports the very same function object, so the lookup could never have differed, and the back-edge to the product layer existed only to keep old monkeypatches alive.

`background: true` schedules through `BackgroundTaskManager` instead and returns a `task_id` immediately. Cancellation differs by design: a background task is not cancelled by the parent's `cancel_event` (its call already returned) and stops only explicitly or via `ConversationRuntime.close()`.

Sub-agents auto-approve their own approval prompts and answer `ask_user` with an empty response. They have no way to reach the user's terminal.

## Goals

Goals are **session-scoped**. `GoalService` (`goal/service.py`) is bound to one vtx session, so a goal created by session A is invisible to every other instance working the same project. Parallel sessions never fight over focus or mutate each other's work.

- Storage is markdown goal files plus an append-only JSONL ledger under `.vtx/goals/` (`storage.py`). `GoalService` is the sole mutation boundary and mutates clones, so a failed write commits nothing.
- Each record carries its owning `session_id`. Ownership is backed by a liveness lease under `.vtx/goals/leases/<session_id>.json`, renewed on every `pool()` read with a 120-second TTL. `claimable()` excludes goals whose owner still holds a lease, so **a running instance never offers another instance's goal away**.
- Goals left by a dead session surface as orphans. The `goal` tool exposes `list_orphans` and `claim`, and the TUI announces claimable goals at startup without claiming or focusing anything.
- Before archiving, `goal/auditor.py` dispatches an independent read-only sub-agent to re-check the objective, the task evidence and the verification contract against the real workspace, ending in `<approved/>` or `<disapproved/>`.

One tool with an `action` parameter covers the lifecycle:

```text
goal(action="create",      objective=..., mode=..., verification=..., token_budget=...)
goal(action="get")
goal(action="update",      status=..., reason=..., completion_summary=..., ...)
goal(action="set_tasks",   tasks=[{title, id?, parent_id?}, ...])
goal(action="update_task", task_id=..., task_status=..., evidence=..., ...)
goal(action="list_orphans")
goal(action="claim",       goal_id=...)
```

All actions operate on the focused goal. Focus is user-owned; no tool can set it.

## Extensions and hooks

`extensions.py` defines the event vocabulary (`MessageStartEvent`, `ToolExecutionEndEvent`, `SessionBeforeCompactEvent`, `ContextEvent`, `ProjectTrustEvent`, ...), the `EventBus` that dispatches them, and the `ExtensionAPI` handed to an extension. `extension_manager.py` discovers extensions and installs them from PyPI or GitHub. `hooks/` adds a second, declarative surface: `.vtx/hooks.yml` parsed by `loader.py` and `registry.py` (`HookConfig`, `HookResult`, `HookSnapshot`), the in-process `AgentHook` protocol in `agent_hook.py` with `AgentHookContext` and `AgentRunHookContext`, and `bridge.py` mapping extension events onto hook events. `CompositeHook` fans one call out to several hooks.

## Configuration split

There is no `config.py` in this package, on purpose.

- Harness tunables - max turns, context-window defaults, compaction and idle policy - are `HarnessConfig` in `vtx/core/harness_config.py`, with product-neutral defaults.
- The user-facing YAML schema and loader are `vtx/core/config.py`, which mirrors user config into the harness object via `apply_harness_settings` on load.

Model catalog and provider adapters are `vtx.ai`. The JavaScript sandbox is `vtx.codemode`.

## Known wart: the duplicated agent-profile package

`vtx/coding_agent/agents/{api,loader,registry,schema}.py` is a **divergent fork** of `vtx/agent/agents/`. All four files differ from their `vtx.agent` counterparts.

Production uses the `vtx.agent` copy: `runtime.py` imports `AgentRegistry` and `LoadedAgent` from there, and `tools/task.py` resolves sub-agent profiles against it. The fork is used by `coding_agent/cli.py` and by six test modules (`tests/test_agent_profiles.py`, `tests/test_agent_profiles_runtime.py`, `tests/test_agent_profiles_tui.py`, `tests/tools/test_task.py`, `tests/test_extension_manager.py`, `tests/ui/test_custom_tool_blocks.py`).

The divergence is not cosmetic. The fork's `AgentDef` declares `tools`, `skills`, `tool_groups` and `active_tool_group` a second time, after a first declaration of the same four fields, so the two copies parse the same keys through different code paths. The fork's `AgentRegistry` also lacks `on_change`, `set_active_tool_group` and `switch`, all of which exist on the `vtx.agent` copy and are used by `ConversationRuntime.cycle_active_tool_group`. Both copies end up with the same 24 Pydantic fields.

Reconciling is a behaviour decision, not a move: someone has to decide whether the duplicate declarations are load-bearing for any profile format in the wild, then delete the loser and repoint the six test modules. Until that is decided, editing `vtx/agent/agents/` alone will silently not change what the CLI sees.

## Two deliberate upward edges

`vtx.agent` imports `vtx.protocol`, `vtx.ai`, `vtx.core` and `vtx.codemode` at module level, plus function-local `vtx.mcp` and `vtx.tui`. Two edges look like violations and are not:

- `vtx.core.config` calls `vtx.agent.subagents.set_limit` to resize the scheduler on config reload.
- `tools/task.py` imports `vtx.tui.blocks.TaskToolBlock` inside the `ui_block` property, and `tools/web.py` imports `vtx.tui.tool_output` escaping helpers inside a formatter. Both name rendering widgets; neither creates an import cycle.

Layering is enforced in CI.
