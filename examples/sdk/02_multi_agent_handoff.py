"""
02_multi_agent_handoff.py — handoffs to a specialist.

Demonstrates a triage agent that delegates to a booking agent. The
handoff tool surfaces as ``transfer_to_booking`` in the triage agent's
tool list; the SDK runner detects it and switches the active agent.
"""

from __future__ import annotations

import asyncio

from vtx.llm.providers.mock import MockProvider
from vtx.sdk import Agent, Runner, handoff

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

triage_agent = Agent(
    name="Triage",
    instructions=(
        "You are the first contact. If the user asks about booking, "
        "hand off to the booking agent. If they ask about refunds, "
        "hand off to the refund agent."
    ),
    provider=MockProvider(scenario="simple_text"),
    handoffs=[
        handoff(booking_agent, tool_description_override="Hand off booking questions."),
        refund_agent,
    ],
)


async def main() -> None:
    tools, by_name = triage_agent.compiled_handoff_tools()
    print("Triage agent handoff tools:")
    for t in tools:
        print(f"  - {t.name} -> {by_name[t.name].name}")
    print()
    # In a real run, the model calls one of these and the SDK switches
    # the active agent. Here we just demonstrate the wiring.
    print("Sample run (mocked; the model doesn't actually invoke handoffs):")
    result = await Runner.run(triage_agent, "I'd like to book a flight to Tokyo.")
    print(f"  Final output: {result.final_output}")
    print(f"  Active agent at the end: {result.agent_name}")


if __name__ == "__main__":
    asyncio.run(main())
