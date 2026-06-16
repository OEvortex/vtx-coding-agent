"""
08_skills.py — load Vtx-format skills into your agent.

The SDK shares Vtx's existing ``.agents/skills/`` discovery, so any
project or user skills you already use with the TUI are available to
your SDK agents.
"""

from __future__ import annotations

import asyncio

from vtx.llm.providers.mock import MockProvider
from vtx.sdk import Agent, Runner
from vtx.sdk.skills import format_skills_for_prompt, load_vtx_skills


async def main() -> None:
    skills = load_vtx_skills()
    if not skills:
        print(
            "No skills found (skip if you don't have any in .agents/skills/ or ~/.agents/skills/)."
        )
    else:
        print(f"Loaded {len(skills)} skills:")
        for s in skills:
            print(f"  - {s.name}: {s.description}")

    # Skills become part of the agent's instructions.
    skills_prompt = format_skills_for_prompt(skills)
    instructions = (
        "You are a helpful assistant.\n\n"
        "When the user asks something that matches a skill below, follow it.\n\n"
        f"{skills_prompt}"
    )

    agent = Agent(
        name="Skillful bot",
        instructions=instructions,
        provider=MockProvider(scenario="simple_text"),
    )

    result = await Runner.run(agent, "Run the review skill")
    print(f"  Final output: {result.final_output}")


if __name__ == "__main__":
    asyncio.run(main())
