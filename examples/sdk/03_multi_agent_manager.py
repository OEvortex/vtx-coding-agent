"""
03_multi_agent_manager.py — manager pattern (agents-as-tools).

The parent agent owns the conversation and invokes a specialist as a
tool. Unlike handoffs, the parent stays in control and combines the
specialist's output with its own reasoning.
"""

from __future__ import annotations

import asyncio

from vtx.llm.providers.mock import MockProvider
from vtx.sdk import Agent, Runner

# Two specialists.
booking_agent = Agent(
    name="Booking",
    instructions="You handle flight and hotel booking questions.",
    provider=MockProvider(scenario="simple_text"),
)

refund_agent = Agent(
    name="Refund",
    instructions="You handle refund questions.",
    provider=MockProvider(scenario="simple_text"),
)

# Manager exposes both as tools.
coordinator = Agent(
    name="Coordinator",
    instructions=(
        "You help customers. Use your tools to look up booking and "
        "refund information when relevant."
    ),
    provider=MockProvider(scenario="simple_text"),
    tools=[
        booking_agent.as_tool(
            tool_name="lookup_booking", tool_description="Look up a customer's existing booking."
        ),
        refund_agent.as_tool(
            tool_name="lookup_refund", tool_description="Look up a customer's refund status."
        ),
    ],
)


async def main() -> None:
    print("Coordinator's tools:")
    for t in coordinator.compiled_tools():
        print(f"  - {t.name}: {t.description}")
    print()
    result = await Runner.run(coordinator, "I want to cancel my booking.")
    print(f"  Final output: {result.final_output}")


if __name__ == "__main__":
    asyncio.run(main())
