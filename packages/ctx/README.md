# ctx-sdk

**A minimalist agent harness for building LLM-powered agents.**

[![PyPI version](https://img.shields.io/pypi/v/ctx-sdk.svg)](https://pypi.org/project/ctx-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/ctx-sdk.svg)](https://pypi.org/project/ctx-sdk/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

`ctx-sdk` is the core agent harness: agent loop, sessions, tools, plugin composition, MCP client, 65 LLM providers, and a Python SDK. The coding agent CLI/TUI is a separate package ([ctx-code](https://pypi.org/project/ctx-code/)) built on top.

## Quick start

```bash
pip install ctx-sdk
```

Python 3.12 or newer.

## Usage

The SDK is the shortest way in. Build an `Agent`, give it a tool, run it:

```python
import asyncio

from ctx.agent.sdk import Agent, Runner, tool


@tool
def get_weather(city: str) -> str:
    """Return the current weather for a city."""
    return f"Sunny in {city}"


agent = Agent(
    name="Weather bot",
    instructions="Be concise.",
    model="gpt-4o-mini",
    tools=[get_weather],
)

result = asyncio.run(Runner.run(agent, "Weather in Tokyo?"))
print(result.final_output)
```

## Packages

| Package | Description |
|---------|-------------|
| **[@ctx.plugins](src/ctx/plugins)** | The plugin kernel: `Context`, `EventHub`, plugin rows, patch layers, profiles, boot composition |
| **[@ctx.protocol](src/ctx/protocol)** | Leaf. Message, stream, and tool-contract types. Imports nothing from `ctx` |
| **[@ctx.telemetry](src/ctx/telemetry)** | Leaf. Spans, processors, console and JSONL exporters |
| **[@ctx.git](src/ctx/git)** | Git and GitHub integration: `gh` CLI wrapper, GitHub App auth |
| **[@ctx.core](src/ctx/core)** | Agent events, permission gate, compaction, config schema and paths, theme registry |
| **[@ctx.ai](src/ctx/ai)** | The LLM layer: provider catalog, OAuth, SDK adapters, model catalog, tool parsing |
| **[@ctx.agent](src/ctx/agent)** | Product-neutral harness: loop, turn engine, sessions, tool registry, prompts, hooks, goals, SDK |

## Optional dependencies

```bash
# Terminal UI building blocks
pip install ctx-sdk[term]

# GitHub App authentication
pip install ctx-sdk[gh-app]
```

## Documentation

Full reference at [github.com/OEvortex/ctx-coding-agent](https://github.com/OEvortex/ctx-coding-agent).

## License

[Apache License 2.0](LICENSE)
