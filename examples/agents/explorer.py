"""Read-only exploration sub-agent.

A reference profile showing how to write a sub-agent the ``Task`` tool
can dispatch. This agent:

* runs without an LLM-readable write surface (the Task tool filters its
  tools_allow against the base pool),
* is read-only by design — the Task tool will still pass through any
  tool the parent has, so an explicit ``tools_allow`` is the right way
  to enforce the read-only contract.

Use it from the TUI::

    /agent explore              # switch the parent into this agent
    Task(subagent_type="explore", ...)

Or directly via the Task tool, without switching the parent's agent::

    Task(
        description="Find auth flow",
        prompt="Trace the login flow and report the entry points.",
        subagent_type="explore",
    )
"""

from vtx.agents import AgentDef

AGENT = AgentDef(
    name="explore",
    description="Read-only repo exploration sub-agent (reference profile).",
    icon="🧭",
    color="cyan",
    thinking_level="medium",
    max_turns=80,
    instructions=(
        "You are a read-only exploration sub-agent. Use find, read, and "
        "grep to map the codebase. Never modify files. When you have "
        "found what was asked, stop and report back concisely."
    ),
    tools_allow=["read", "find", "skill", "web"],
    tools_deny=["bash", "write", "edit"],
    permission_mode="auto",
    handoff_back=True,
    metadata={"owner": "examples", "kind": "subagent"},
)
