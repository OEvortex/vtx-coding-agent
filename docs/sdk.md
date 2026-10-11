# SDK

The SDK is the programmatic interface to the Vtx agent runtime. This page covers the top-level exports, the `Agent` and `Runner` classes, tool registration, guardrails, handoffs, approvals, permissions, sessions, and tracing, ending with a minimal program that runs as written.

The SDK drives the same engine the TUI and headless modes use  --  the native turn runner's event stream  --  and layers guardrails, handoffs, approvals, and session persistence on top of it (`src/vtx/agent/sdk/runner.py:377-433`). Nothing here needs the TUI, and none of it requires an extensions graph. If you want the composition of subsystems the CLI boots, read [plugins.md](plugins.md); if you are looking for the built-in tools, they are in [tools.md](tools.md).

## Minimal program

This is the SDK's own quick start, and it runs as written against a configured provider (`src/vtx/agent/sdk/__init__.py:8-25`):

```python
from vtx.agent.sdk import Agent, Runner, tool


@tool
def get_weather(city: str) -> str:
    """Look up the current weather for a city."""
    return f"Sunny in {city}"


agent = Agent(
    name="Weather bot",
    instructions="Be concise.",
    model="gpt-4o-mini",
    tools=[get_weather],
)

result = Runner.run_sync(agent, "Weather in Tokyo?")
print(result.final_output)
```

`Runner.run_sync` runs the coroutine on a one-worker thread pool, so it is safe to call from inside an event loop you already own (`src/vtx/agent/sdk/runner.py:295-325`).

## Top-level exports

`from vtx import ...` re-exports the whole agent surface. Grouped by what they do:

| Group | Names |
|---|---|
| Core | `Agent`, `Runner`, `RunResult`, `RunStreamed`, `RunConfig`, `RunItem`, `Usage` |
| Tools | `tool`, `FunctionTool`, `Handoff`, `handoff` |
| Permissions | `PermissionPolicy`, `PermissionDecision`, `AutoApprove`, `AllowlistApprove`, `PromptApprove` |
| Approvals | `ToolApprovalItem`, `RunState` |
| Guardrails | `InputGuardrail`, `OutputGuardrail`, `input_guardrail`, `output_guardrail`, `tool_input_guardrail`, `tool_output_guardrail`, `GuardrailFunctionOutput`, `ToolGuardrailFunctionOutput`, `InputGuardrailTripwireTriggered`, `OutputGuardrailTripwireTriggered`, `ToolInputGuardrailTripwireTriggered`, `ToolOutputGuardrailTripwireTriggered` |
| Sessions | `Session`, `InMemorySession`, `JSONLSession`, `SessionSettings` |
| Items | `MessageOutputItem`, `ToolCallItem`, `ToolCallOutputItem`, `HandoffCallItem`, `HandoffOutputItem`, `ReasoningItem` |
| Tracing | `Trace`, `Span`, `trace`, `span`, `add_trace_processor`, `enable_tracing`, `disable_tracing`, `TraceProcessor`, `ConsoleTraceProcessor`, `JSONLTraceProcessor` |

`vtx` also exports the headless entry points (`run_headless`, `render_run`, `resolve_prompt`)  --  see [headless.md](headless.md).

## Runner

Three entry points, all static, all with the same argument shape (`src/vtx/agent/sdk/runner.py:265-345`):

```python
@staticmethod
async def run(
    starting_agent: Agent,
    input: str | list[Any],
    *,
    session: Session | None = None,
    run_config: RunConfig | None = None,
    max_turns: int | None = None,
    cancellation: asyncio.Event | None = None,
    context: Any = None,
) -> RunResult: ...

# run_sync(...) -> RunResult          same arguments, blocking
# run_streamed(...) -> RunStreamed    same arguments, async-iterable
```

| Argument | Meaning |
|---|---|
| `starting_agent` | The `Agent` that receives the first turn |
| `input` | A string, or a list of input items |
| `session` | Where conversation history lives. A fresh `InMemorySession` is created if omitted (`runner.py:61`) |
| `run_config` | Per-run `RunConfig` |
| `max_turns` | Model turns before stopping. Falls back to `run_config.max_turns`, then to `50` (`runner.py:282`) |
| `cancellation` | An `asyncio.Event`; setting it aborts the run |
| `context` | Opaque object passed through to guardrails and tools |

A run wraps a `Trace` and a `span("agent_run", agent_name=...)`, so tracing works without extra setup (`runner.py:377-433`).

`RunResult` (`src/vtx/agent/sdk/results.py:13-56`):

| Field | Meaning |
|---|---|
| `final_output` | The final output. A string, or a Pydantic model when `Agent(output_type=...)` |
| `new_items` | Every `RunItem` produced during the run, in order |
| `interruptions` | `ToolApprovalItem`s needing a decision. Empty unless the run paused |
| `state` | A `RunState` snapshot; `None` unless the run paused. See [Approvals](#approvals) for the caveat on resuming |
| `stop_reason` | `STOP`, `TOOL_USE`, `LENGTH`, `ERROR`, or `INTERRUPTED` |
| `usage` | Token usage aggregated across turns |
| `agent_name` | The name of the agent that produced the final output |

### Streaming

`run_streamed` returns a `RunStreamed` you iterate for typed events (`src/vtx/agent/sdk/runner.py:187-263`):

```python
from vtx import Agent, Runner

stream = Runner.run_streamed(agent, "Summarize this repo asynchronously")

async for event in stream:
    if type(event).__name__ == "_TextDelta":
        print(event.delta, end="", flush=True)
    elif type(event).__name__ == "_ToolCallStart":
        print(f"\n-> {event.tool_name}")

result = stream.result  # only valid after the async for exhausts
print("\n", result.final_output, result.usage)
```

| Event | Fields |
|---|---|
| `_AgentStartEvent` | `agent_name` |
| `_TextDelta` | `delta` |
| `_ToolCallStart` | `tool_call_id`, `tool_name` |
| `_ToolCallEnd` | `tool_call_id`, `tool_name`, `arguments` |
| `_ToolResult` | `tool_call_id`, `tool_name`, `output`, `is_error` |
| `_RunFinished` | `result`  --  consumed by `RunStreamed`, not yielded to you |

Reading `.result` before the iterator is exhausted raises a `RuntimeError` telling you to iterate first (`src/vtx/agent/sdk/runner.py:250-258`).

## Agent

`Agent` is a mutable dataclass, so `clone(**overrides)` gives you a variant without touching the original (`src/vtx/agent/sdk/agent.py:95-179`):

```python
@dataclass
class Agent:
    name: str
    instructions: str | Callable[[Any], str] | None = None
    model: str | None = None
    provider: Any | dict[str, Any] | None = None
    tools: list[Any] = field(default_factory=list)
    handoffs: list[Any] = field(default_factory=list)
    output_type: type[BaseModel] | None = None
    input_guardrails: list[InputGuardrail] = field(default_factory=list)
    output_guardrails: list[OutputGuardrail] = field(default_factory=list)
    tool_use_behavior: str = "run_llm_again"
    metadata: dict[str, Any] = field(default_factory=dict)
    needs_approval_tools: set[str] = field(default_factory=set)
```

(`src/vtx/agent/sdk/agent.py:168-179`)

| Field | Meaning |
|---|---|
| `name` | Required. Must be truthy; `__post_init__` raises otherwise (`agent.py:182-189`) |
| `instructions` | A string, or a callable receiving the run context |
| `model` | Model id, falling back to config when `None` |
| `provider` | A provider object or a dict. See below |
| `tools` | Functions from `@tool`, `BaseTool` instances, or another `Agent` |
| `handoffs` | `handoff(...)` results |
| `output_type` | A Pydantic model to validate `final_output` against |
| `input_guardrails`, `output_guardrails` | Guardrail lists |
| `tool_use_behavior` | `run_llm_again` (default) or `stop_on_first_tool` |
| `metadata` | Free-form; visible to guardrails and traces |
| `needs_approval_tools` | Tool names that pause for approval even under a permissive policy |

Methods: `compiled_tools`, `compiled_handoff_tools`, `all_tools`, `as_tool`, `clone`, `resolve_instructions`, `build_system_prompt`, `resolve_provider` (`src/vtx/agent/sdk/agent.py:195-521`).

### Providers

`provider` accepts a live provider object, or a dict with any of `_PROVIDER_DICT_KEYS` (`src/vtx/agent/sdk/agent.py:42-54`):

```python
{"name", "sdk", "api_key", "base_url", "model", "max_tokens", "temperature", "thinking_level", "default_headers"}
```

`resolve_provider` picks in this order (`src/vtx/agent/sdk/agent.py:374-521`): a `BaseProvider` passes through untouched; a dict whose `name` matches the built-in provider catalog is built from that; a dict with a custom `name` requires both `sdk` and `base_url` and raises otherwise; anything else falls back to the environment-var and config path the CLI uses, which is why the quick-start program needs no `provider` at all.

## Tool registration

`@tool` wraps a sync or async function and builds a Pydantic params model from its type hints and docstring (`src/vtx/agent/sdk/tools.py:252-312`):

```python
from vtx import tool

@tool(needs_approval=True, mutating=True)
def delete_file(path: str) -> str:
    """Delete a file permanently.

    :param path: Path to the file to remove.
    """
    ...
```

| Option | Default | Meaning |
|---|---|---|
| `name` | Function name | Override the tool name |
| `description` | Docstring summary | Override the description |
| `needs_approval` | `False` | Pause for human approval before running |
| `mutating` | `True` | `False` marks the tool read-only so a permission policy can skip it |
| `tool_icon` | `"→"` | Badge shown in the TUI |
| `input_guardrails` / `output_guardrails` | `None` | Per-tool guardrails |

Docstring formats understood: Sphinx `:param name:`, Google `Args:`, and NumPy `Parameters:` (`src/vtx/agent/sdk/tools.py:79-112`). A `None` return is normalized to `"(no output)"`, and an exception becomes `ToolResult(success=False, result=f"Error: {exc}")` rather than aborting the turn.

An agent can be used as a tool too, with `agent.as_tool(...)` (`src/vtx/agent/sdk/agent.py:266-291`). That returns a `_AgentAsTool` with `mutating = False` (`src/vtx/agent/sdk/tools.py:320-340`), so a nested run is not a write. To send the transcript onward, prefer a [handoff](#handoffs).

## Guardrails

Four decorators, two data contracts. Decorate a function and pass the result into an agent (`src/vtx/agent/sdk/guardrails/__init__.py:29-152`):

| Decorator | Put it on | Data it receives | Tripwire raised |
|---|---|---|---|
| `@input_guardrail` | An agent's `input_guardrails` | `context`, `agent`, `input` | `InputGuardrailTripwireTriggered` |
| `@output_guardrail` | An agent's `output_guardrails` | `context`, `agent`, `output` | `OutputGuardrailTripwireTriggered` |
| `@tool_input_guardrail` | A tool (via `@tool(input_guardrails=[...])`) | `context`, `tool_name`, `tool_arguments` | `ToolInputGuardrailTripwireTriggered` |
| `@tool_output_guardrail` | A tool (via `@tool(output_guardrails=[...])`) | `context`, `tool_name`, `tool_result` | `ToolOutputGuardrailTripwireTriggered` |

Guardrails for the same target run concurrently via `asyncio.gather`. When timing matters, remember where each runs:

- Input guardrails fire **before** the first model call, so a trip there never costs a token.
- Output guardrails fire after the final output is produced.
- A tool-input guardrail skips the tool; the model is told it was blocked and keeps going.
- A tool-output guardrail replaces the result the model sees.

An agent-level guardrail and a tool-level one differ in return type. Agent guardrails return a `GuardrailFunctionOutput`; tool guardrails return a `ToolGuardrailFunctionOutput`.

```python
from vtx import Agent, GuardrailFunctionOutput, input_guardrail


@input_guardrail
def no_profanity(data):
    """Abort the run before any model call if the input is abusive."""
    if "damn" in data.input.lower():
        return GuardrailFunctionOutput(
            output_info={"reason": "abusive language"},
            tripwire_triggered=True,
        )
    return GuardrailFunctionOutput()


agent = Agent(
    name="Polite bot",
    instructions="Be courteous.",
    model="gpt-4o-mini",
    input_guardrails=[no_profanity],
)
```

The two output types (`src/vtx/agent/sdk/guardrails/types.py:12-51`):

```python
@dataclass
class GuardrailFunctionOutput:
    output_info: Any = None
    tripwire_triggered: bool = False


@dataclass
class ToolGuardrailFunctionOutput:
    output_info: Any = None
    tripwire_triggered: bool = False

    @classmethod
    def allow(cls, output_info: Any = None) -> ToolGuardrailFunctionOutput: ...
    @classmethod
    def reject_content(cls, message: str, output_info: Any = None) -> ToolGuardrailFunctionOutput: ...
    @classmethod
    def raise_exception(cls, output_info: Any = None) -> ToolGuardrailFunctionOutput: ...
```

`reject_content` is the usual choice: it gives the model something actionable instead of ending the run, and the runner reads the `message` back into the tool result as `{"rejected_message": ...}` (`src/vtx/agent/sdk/guardrails/types.py:37-47`). Use `raise_exception` when a result must not reach the model at all.

The data payloads are the dataclasses `_InputGuardrailData`, `_OutputGuardrailData`, `_ToolInputGuardrailData`, and `_ToolOutputGuardrailData` (`src/vtx/agent/sdk/guardrails/types.py:54-84`). Your decorated function takes the same fields in the same order, so name the parameters as they appear. Note that `_ToolInputGuardrailData.tool_arguments` is a **JSON string** (`str | None`), not a dict  --  parse it before inspecting it.

## Handoffs

`handoff` turns one agent into a transfer target for another (`src/vtx/agent/sdk/handoffs.py:43-79`):

```python
from vtx import Agent, handoff

billing = Agent(name="Billing", instructions="Handle refunds.", model="gpt-4o-mini")
support = Agent(
    name="Support",
    instructions="Route billing questions.",
    handoffs=[handoff(billing, tool_description_override="Transfer a refund request")],
)
```

`Handoff` is a `BaseTool` with `mutating = False` and icon `↪`, and its params model accepts an optional free-form `reason` (`src/vtx/agent/sdk/handoffs.py:82-131`).

| Option | Meaning |
|---|---|
| `agent` | The `Agent` to transfer to |
| `tool_name_override` | Default is `transfer_to_<agent.name.lower().replace(' ','_')>` (`handoffs.py:109`) |
| `tool_description_override` | |
| `on_handoff` | Callback with the handoff context |
| `input_filter` | Drop items from the transcript before the transfer |
| `input_type` | A Pydantic model the target receives instead of the raw transcript |

The runner detects a handoff by tool name: anything starting with `transfer_to_` (`src/vtx/agent/sdk/runner.py:691`). On transfer, the active agent swaps and a synthetic user note tells the target agent to take over (`runner.py:719-726`):

```text
[handoff from Support → Billing. Take over the conversation from here.]
```

That is why every handoff tool name must keep the `transfer_to_` prefix  --  the prefix is the protocol. With `RunConfig(nest_handoff_history=True)`, the prior transcript collapses into a single summary block instead of being carried forward (`src/vtx/agent/sdk/run_config.py:40-41`).

## Approvals

A tool marked `needs_approval=True`, or named in `Agent.needs_approval_tools`, pauses the run instead of calling (`src/vtx/agent/sdk/approvals.py:15-104`). The `Runner` returns with `interruptions` populated, `stop_reason=StopReason.TOOL_USE`, and a `state` snapshot (`src/vtx/agent/sdk/runner.py:479-498`):

```python
result = Runner.run_sync(agent, "delete the README")

if result.interruptions:
    state = result.state
    call = result.interruptions[0]
    print(f"{call.name}({call.call_id}) wants to run")
    state.approve(call) if confirmed_by_human(call) else state.reject(call)
```

`RunState` carries the original input, the pending tool calls, the decisions you queue, the new items, and free-form metadata (`src/vtx/agent/sdk/approvals.py:63-104`):

| Member | Meaning |
|---|---|
| `original_input` | The input that started the run |
| `pending_tool_calls` | `ToolCall`s awaiting a decision |
| `decisions` | Queued `_PendingDecision`s |
| `new_items` | Items generated so far, for inspection |
| `metadata` | Free-form |
| `approve(*items)` / `reject(*items)` | Queue decisions |
| `decision_for(call_id)` | Consume and return one decision |

A `ToolApprovalItem`'s `to_input_item` raises  --  an approval prompt is never replayed into a model request (`approvals.py:45`). A rejected call returns a "denied by user" tool result so the model can pick another path (`src/vtx/agent/README.md:96`).

## Permissions

Permissions decide *before* approval: `ALLOW` runs, `PROMPT` means a human must look (`src/vtx/agent/sdk/permissions.py:14-16`).

| Policy | Behavior |
|---|---|
| `AutoApprove` | Every tool is `ALLOW` (`permissions.py:39-43`) |
| `AllowlistApprove(allowlist)` | A name in the list is `ALLOW`, everything else `PROMPT` (`permissions.py:46-55`) |
| `PromptApprove` (default) | `mutating=False` → `ALLOW`; an allowlisted `bash` head → `ALLOW`; else `PROMPT` (`permissions.py:58-100`) |

`PromptApprove.SAFE_BASH` is the read-only command list: `cat`, `head`, `tail`, `ls`, `pwd`, `wc`, `diff`, `which`, `file`, `stat`, `du`, `df`, `whoami`, `id`, `uname`, `date`, `realpath`, `dirname`, `basename` (`permissions.py:66-89`). Anything else under `bash` prompts.

A plain callable works too: `PermissionCallback` coerces a returned string to the enum and defaults to `PROMPT` (`src/vtx/agent/sdk/permissions.py:104-122`). Override the policy for one run with `RunConfig.permission_policy`. For what the TUI saves to `config.yml` and what the modes mean, read [permissions.md](permissions.md).

## Sessions

Four methods make a `Session` (`src/vtx/agent/sdk/sessions.py:31-60`):

```python
session.get_items(limit=None)   # list[Any]
session.add_items(items)        # None
session.pop_item()              # Any | None
session.clear_session()         # None
```

| Implementation | Use |
|---|---|
| `InMemorySession(session_id=None)` | A run and its history die together (`sessions.py:63-72`) |
| `JSONLSession(path=None, *, session_id=None)` | Appends to disk, and the same file the TUI and headless runs produce |
| `SessionSettings` | Per-session settings such as an item `limit` (`sessions.py:23`) |

Cross-process sharing is the useful property: a `JSONLSession` pointed at a session written by a `vtx -p` run gives you that conversation to continue programmatically. See [storage-layout.md](storage-layout.md) for where those files land.

`JSONLSession` also exposes `session_id` and `path` properties if you need to log them (`sessions.py:220-227`). Skill discovery helpers live alongside: `load_vtx_skills(cwd)`, `load_builtin_vtx_skills()`, and `format_skills_for_prompt(skills)` (`src/vtx/agent/sdk/skills.py:15-42`); the frontmatter rules are in [skills.md](skills.md).

## Tracing

Every run opens a `Trace` and a nested `Span`, so a processor registered once sees every run in the process (`src/vtx/agent/sdk/runner.py:377-433`). The default processor list is empty until you add one (`src/vtx/telemetry/processor.py:28-30`).

```python
from vtx import add_trace_processor, ConsoleTraceProcessor, enable_tracing

enable_tracing()
add_trace_processor(ConsoleTraceProcessor())
```

```text
[trace] ▶ agent_run (trace_3f1c...)
    [span] ▶ llm_call (span_8ab2...)
    [span] ■ llm_call (span_8ab2...) 1204.3ms
[trace] ■ agent_run (trace_3f1c...) 2421.0ms
```

| Symbol | Where |
|---|---|
| `Trace`, `trace(...)` | `Trace` is a context manager; `trace(...)` is a context manager *or* decorator, and a nested trace becomes a span of its parent (`tracing_impl.py:69-113`, `173-249`) |
| `span(...)` | Context manager for one operation (`tracing_impl.py:141-171`) |
| `current_span()`, `current_trace()` | The active objects, via contextvars (`tracing_impl.py:112`, `136-139`) |
| `TraceProcessor` | Protocol: `on_trace_start`, `on_trace_end`, `on_span_start`, `on_span_end` (`processor.py:9-21`) |
| `ConsoleTraceProcessor` | `[trace]` / `[span]` lines to stderr with millisecond durations (`exporters/console.py:12-45`) |
| `JSONLTraceProcessor(path)` | One JSON object per event; parents are created. **It truncates the file on construction** (`exporters/jsonl.py:13-25`) |
| `enable_tracing()` / `disable_tracing()` | Global switch (`tracing_impl.py:48`, `53`) |
| `add_trace_processor(p)` | Appends to the global chain (`tracing_impl.py:58-66`) |

Set `VTX_SDK_DISABLE_TRACING` to `1`, `true`, `yes`, or `on` in the environment to make `span()` a no-op across the process (`src/vtx/telemetry/tracing_impl.py:33-49`). Per run, `RunConfig.tracing_disabled=True` and `RunConfig.trace_include_sensitive_data=False` suppress the trace or strip inputs and outputs from it (`src/vtx/agent/sdk/run_config.py:33-38`).

## RunConfig

| Field | Meaning |
|---|---|
| `max_turns` | Model turns before stopping |
| `session_input_callback` | `(history, new_input) -> final list`; control exactly what the model sees (`run_config.py:21-27`) |
| `session_settings` | Per-run `SessionSettings` override |
| `tracing_disabled` | Skip this run's trace |
| `trace_include_sensitive_data` | Include model inputs and outputs in the trace |
| `permission_policy` | Per-run policy override |
| `nest_handoff_history` | Collapse prior transcript on handoff |
| `custom` | Free-form data attached to the run |

(`src/vtx/agent/sdk/run_config.py:13-46`)

## Worked example

A two-agent setup with a guarded tool, a handoff, an approval, and JSONL tracing. This runs as written once a provider is configured:

```python
import json

from vtx import (
    Agent, AllowlistApprove, JSONLTraceProcessor, RunConfig, Runner,
    ToolGuardrailFunctionOutput, add_trace_processor, handoff, tool, tool_input_guardrail,
)


@tool_input_guardrail
def block_private_locations(context, tool_name, tool_arguments):
    args = json.loads(tool_arguments or "{}")
    if str(args.get("city", "")).startswith("Private-"):
        return ToolGuardrailFunctionOutput.reject_content(
            "I cannot look up locations marked private."
        )
    return ToolGuardrailFunctionOutput.allow()


@tool(mutating=False, input_guardrails=[block_private_locations])
def get_weather(city: str) -> str:
    """Look up the current weather for a city.

    :param city: City name.
    """
    return f"Sunny, 21C in {city}"


billing = Agent(
    name="Billing",
    instructions="You refund charges. Restrained and terse.",
    model="gpt-4o-mini",
)

support = Agent(
    name="Support",
    instructions="Answer weather questions. Transfer anything about a refund "
    "to the billing agent.",
    model="gpt-4o-mini",
    tools=[get_weather],
    handoffs=[handoff(billing)],
)

add_trace_processor(JSONLTraceProcessor("/tmp/vtx-trace.jsonl"))

run_config = RunConfig(permission_policy=AllowlistApprove(["get_weather"]))

result = Runner.run_sync(support, "Weather in Tokyo?", run_config=run_config)
print(result.final_output)
print(result.agent_name, result.usage)
```

`get_weather` is `mutating=False` and allowlisted, so no prompt fires; change it to `mutating=True` and drop it from the allowlist, or pass `needs_approval=True`, and the same program pauses with a `RunState` to resume from as shown under [Approvals](#approvals). The trace lands in `/tmp/vtx-trace.jsonl` with one object per trace and span event.

## Known gaps

- The streamed event classes are underscore-prefixed (`_TextDelta`, `_ToolCallStart`, ...), so matching on the type name is the only stable dispatch. They are not exported from `vtx` (`src/vtx/agent/sdk/runner.py:186-221`).
- `Agent` is generic over a context type (`Agent[TContext]`), but `context` is passed as `Any` at runtime and there is no runtime check that the type parameters agree (`src/vtx/agent/sdk/agent.py:95`).
- A handoff is recognized *only* by the `transfer_to_` tool-name prefix (`runner.py:691`); an override that breaks that prefix silently becomes an ordinary tool call.
- `RunState`'s docstring says to "pass this object back to `Runner.run`", but `Runner.run`, `run_sync`, and `run_streamed` accept no `state` argument (`src/vtx/agent/sdk/runner.py:269-276`, `295-301`, `329-336`), and `decision_for` is never called from the runner. The run pauses and hands you the decisions; continuing after them is not wired up.
- The default processor list is empty until you add one, so a run with no `add_trace_processor` produces no trace output at all (`src/vtx/telemetry/processor.py:28-30`).
- `Agent.as_tool` spawns a nested run with no recursion guard. An agent that lists itself as its own tool will recurse until `max_turns` runs out.
