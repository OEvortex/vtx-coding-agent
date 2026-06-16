"""
01_quickstart.py — the simplest possible SDK run.

    uv run python examples/sdk/01_quickstart.py

Uses a mock provider so it runs offline (no API key required).
"""

from __future__ import annotations

import asyncio

from vtx.llm.providers.mock import MockProvider
from vtx.sdk import Agent, Runner, tool


@tool
def get_weather(city: str) -> str:
    """Return the current weather for a city."""
    return f"Sunny in {city}"


# A real run would pass a provider dict like:
#
#     agent = Agent(
#         name="Weather bot",
#         instructions="Be concise.",
#         model="gpt-4o-mini",
#         provider={
#             "sdk": "openai",                    # or "anthropic", "kilo", etc.
#             # "api_key": "sk-...",                # optional, uses env by default
#             # "base_url": "https://...",         # optional, uses provider default
#         },
#         tools=[get_weather],
#     )
#
# For an offline demo, we use a mock provider.
agent = Agent(
    name="Weather bot",
    instructions="Be concise.",
    provider=MockProvider(scenario="simple_text"),
    tools=[get_weather],
)


async def main() -> None:
    result = await Runner.run(agent, "What's the weather in Tokyo?")
    print("--- final output ---")
    print(result.final_output)
    print("--- new_items ---")
    for item in result.new_items:
        print(f"  - {item.__class__.__name__}")


if __name__ == "__main__":
    asyncio.run(main())
