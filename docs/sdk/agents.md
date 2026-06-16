# Agents

An `Agent` is the unit of work in the SDK: a model, instructions,
tools, and (optionally) handoffs.

## Construction

```python
from vtx.sdk import Agent

agent = Agent(
    name="My agent",
    instructions="You are helpful and concise.",
    model="gpt-4o-mini",
    tools=[...],
    handoffs=[...],
    input_guardrails=[...],
    output_guardrails=[...],
    output_type=MyPydanticModel,  # optional
)
```

| Field | Required | Description |
|---|---|---|
| `name` | yes | Human-readable identity. Surfaced in traces, tools, handoffs. |
| `instructions` | recommended | System prompt. String or `Callable[[Context], str]`. |
| `model` | yes (or `provider.model`) | Model identifier. The SDK resolves a provider. |
| `provider` | no | `BaseProvider`, a dict, or `None`. See below. |
| `tools` | no | List of `BaseTool` / `FunctionTool` / `Agent` / callable. |
| `handoffs` | no | List of `Agent` or `Handoff`. Becomes handoff tools. |
| `output_type` | no | Pydantic model. The runner validates the final output against it. |
| `input_guardrails` | no | Input guardrails from `@input_guardrail`. |
| `output_guardrails` | no | Output guardrails from `@output_guardrail`. |
| `metadata` | no | Free-form dict for app-side bookkeeping. |

## The `provider` field

`provider` is the single place to specify how the agent talks to an
LLM. It accepts three shapes:

**1. `None` (default) — resolve from env vars.** The SDK uses
`self.model` for the model name and looks up `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, etc. as appropriate.

```python
agent = Agent(
    name="Bot",
    model="gpt-4o-mini",
    # provider=None
)
```

**2. A `BaseProvider` instance — full control.** Use this for tests
(mock provider), custom transports, or any case where you want to
pre-build the provider yourself.

```python
from vtx.llm.providers.mock import MockProvider

agent = Agent(
    name="Bot",
    provider=MockProvider(scenario="simple_text"),
)
```

**3. A dict — the common case.** The dict shape depends on whether
the provider is a **built-in** (declared in Vtx's `provider.yaml`)
or a **custom** / non-builtin endpoint:

**Built-in providers** — just `name` (the provider slug from
`provider.yaml`) and `api_key`. The catalog knows the SDK mode, the
base URL, and the available models. Plus any `ProviderConfig` field
as override (`thinking_level`, `max_tokens`, `temperature`, …).

```python
agent = Agent(
    name="Bot",
    model="gpt-4o-mini",
    provider={
        "name": "openai",                  # or "anthropic", "kilo", "ollama", ...
        "api_key": "sk-...",                # optional, uses env by default
        # "thinking_level": "low",         # optional override
        # "max_tokens": 4096,              # optional override
        # "temperature": 0.7,               # optional override
        # "default_headers": {"X-Foo": "bar"},  # optional
    },
)
```

**Custom / non-builtin providers** — you must supply all four
fields: `name` (your identifier), `sdk` (the SDK transport mode:
`"openai"`, `"anthropic"`, …), `base_url` (the endpoint), and
`api_key`. The SDK builds the transport from the explicit
`sdk` + `base_url` and never touches the catalog.

```python
agent = Agent(
    name="Local bot",
    model="llama-3",
    provider={
        "name": "my-local-llm",
        "sdk": "openai",                   # the transport
        "api_key": "anything",             # often unused for local servers
        "base_url": "http://localhost:11434/v1",
    },
)
```

If you pass a `name` that isn't a Vtx built-in but you forget
`sdk` or `base_url`, you'll get a clear `ValueError`.

## System prompt composition

`Agent.build_system_prompt()` produces the system prompt that the LLM
sees. Layout (in order):

1. The agent's `instructions`.
2. Tool-usage guidelines, aggregated from each tool's
   `prompt_guidelines` tuple.
3. An output-format section (only when `output_type` is set).

## Cloning

Use `agent.clone(...)` to derive a new agent with field overrides:

```python
spanish_agent = english_agent.clone(
    name="Spanish tutor",
    instructions="Always respond in Spanish.",
)
```

The original agent is unchanged.

## Agent-as-tool (manager pattern)

To expose an agent as a callable tool, use `as_tool()`:

```python
sub = Agent(name="Specialist", instructions="x", tools=[...])
parent = Agent(
    name="Manager",
    tools=[sub.as_tool(tool_name="ask_specialist")],
)
```

When the parent calls the tool, the sub-agent runs to completion and
its `final_output` is returned as the tool result. The parent stays in
control.

See [`multi_agent.md`](multi_agent.md) for the alternative **handoff**
pattern, where control transfers to the target.
