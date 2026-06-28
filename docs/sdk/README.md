# VTX Agentic SDK

The VTX Agentic SDK is Vtx's programmatic, multi-agent interface. It
exposes Vtx's lean runtime (~2k-token core prompt), 18+ LLM provider
catalog, and Pydantic-typed tool system as a clean Python API you can
build agentic applications on top of.

## Quick start

```python
from vtx.sdk import Agent, Runner, tool

@tool
def get_weather(city: str) -> str:
    """Return the current weather for a city."""
    return f"Sunny in {city}"

agent = Agent(
    name="Weather bot",
    instructions="Be concise.",
    model="gpt-4o-mini",
    provider={"name": "openai"},   # uses OPENAI_API_KEY from env
    tools=[get_weather],
)

result = Runner.run_sync(agent, "Weather in Tokyo?")
print(result.final_output)
```

The `provider` field is a single parameter that accepts:

* A `BaseProvider` instance (full control).
* A dict — built-in providers only need `{name, api_key}` (the
  catalog knows the rest). Custom / non-builtin providers need
  `{name, sdk, base_url, api_key}`.
* `None` (default) — fall back to env vars and `self.model`.

A runnable, fully-offline version (using a mock provider) lives at
[`examples/sdk/01_quickstart.py`](../../examples/sdk/01_quickstart.py).

## Core concepts

| Concept | What it is | Docs |
|---|---|---|
| `Agent` | LLM + instructions + tools + (optional) handoffs | [`agents.md`](agents.md) |
| `Runner` | The single entry point for running an agent | [`runner.md`](runner.md) |
| `tool` | Decorator that turns a Python function into a tool | [`tools.md`](tools.md) |
| `handoff` / `Agent.as_tool` | Multi-agent delegation primitives | [`multi_agent.md`](multi_agent.md) |
| `Session` | Pluggable memory backends (InMemory, JSONL) | [`sessions.md`](sessions.md) |
| `Guardrails` | Input / output / tool-level checks | [`guardrails.md`](guardrails.md) |
| `Approvals` | Pause for human review mid-run | [`approvals.md`](approvals.md) |
| `Tracing` | Trace + span primitives, processor chain | [`tracing.md`](tracing.md) |
| `Permissions` | Pluggable tool-call permission policy | [`permissions.md`](permissions.md) |
| `Skills` | Load Vtx `.agents/skills/` into your agent | [`skills.md`](skills.md) |

## Related: handoff agents

The SDK's `Agent` and the runtime's **handoff agents**
(`.vtx/agent/<name>.py`, switchable via `Shift+Tab` in the TUI) are
related but now partially overlapping concepts:

* The SDK's `Agent` is a **first-class multi-agent primitive** — `Runner`
  orchestrates one or more `Agent` instances into a run, with explicit
  `handoff` and `as_tool` delegation.
* Handoff agents are **switchable TUI profiles** that bundle instructions,
  tool allow/deny, optional model overrides, and agent-scoped tools. They
  are described in the user doc: [../agents.md](../agents.md).

The bridge between them is the `tools` field on `AgentDef`: a handoff
profile can embed raw SDK tools (`@tool` callables or `BaseTool` instances)
or even full SDK `Agent` instances (exposed as manager-pattern tools) without
writing a `register(api)` function. See [../agents.md](../agents.md) for the
`tools`, `tool_groups`, and `skills` fields.

If you're building an agentic application on top of the SDK, you'll
mostly use the `Agent` / `Runner` primitives. If you're customizing the
TUI workflow with reusable profiles, use handoff agents.

## Why a separate SDK?

Vtx's TUI is opinionated: it's built for the coding-agent loop, and
the system prompt is tuned for that. The SDK is the same runtime, but
the system prompt is whatever you pass via `instructions`, and you get
the full event stream for UIs / automation / testing.

Differentiators vs. `openai-agents`:

- **~2k-token core prompt** (Vtx is the leanest agentic runtime in Python)
- **18+ providers out of the box** (OpenAI, Anthropic, Azure, Copilot, DeepSeek, Kilo, OpenCode, TokenRouter, Zhipu, Ollama, llama-server, …)
- **Markdown-driven skills** (`.agents/skills/`, `register_cmd: true` slash commands)
- **Branchable JSONL sessions** (`.tree` style tree navigation)
- **Permissions as a pure function** (`PermissionPolicy` Protocol)

## Compatibility

The SDK ships inside the existing `vtx-coding-agent` wheel. TUI, CLI,
and SDK users all share the same tool registry, LLM providers, and
session format. A session created by the SDK can be resumed from the
TUI and vice versa.

If you want a slimmer install footprint, the TUI dependencies
(Textual, Pillow) are not required to use the SDK.
