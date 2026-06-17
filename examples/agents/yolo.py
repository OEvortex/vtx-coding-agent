"""Minimal-overhead implementation agent.

Pairs down the agent's safety nets: it skips approval prompts and runs
with a high turn cap. Use this for trusted, well-scoped work.
"""

from vtx.agents import AgentDef

AGENT = AgentDef(
    name="yolo",
    description="Minimal-overhead implementation mode (no confirmations)",
    icon="⚡",
    thinking_level="medium",
    max_turns=1000,
    instructions=(
        "Bias toward action. Use the smallest correct change. "
        "When the user asks for an implementation, write it; do not describe it."
    ),
    instructions_mode="append",
    permission_mode="auto",
    handoff_back=True,
)
