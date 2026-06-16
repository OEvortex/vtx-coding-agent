"""
06_approvals.py — human-in-the-loop with ``needs_approval=True``.

When a tool is marked ``needs_approval=True``, the SDK pauses the run
and returns a :class:`RunResult` with ``interruptions``. You can then
decide which calls to approve or reject and resume with a ``RunState``.
"""

from __future__ import annotations

import asyncio

from vtx.llm.providers.mock import MockProvider
from vtx.sdk import Agent, Runner, tool


@tool(needs_approval=True)
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email. Requires approval before execution."""
    return f"sent to {to}: {subject}"


agent = Agent(
    name="Email bot",
    instructions="Send emails on the user's behalf.",
    provider=MockProvider(scenario="simple_text"),
    tools=[send_email],
)


async def main() -> None:
    # The mock provider doesn't actually call the tool, so no approval
    # pause is triggered. We demonstrate the wiring instead.
    print("Agent has needs_approval tools:")
    for t in agent.compiled_tools():
        if t.needs_approval:
            print(f"  - {t.name} (approval required)")
    print()
    result = await Runner.run(agent, "Send a confirmation email")
    print(f"  Final output: {result.final_output}")
    print(f"  Interruptions: {len(result.interruptions)}")
    print(f"  RunState: {result.state!r}")


if __name__ == "__main__":
    asyncio.run(main())
