"""Data-engineering profile demonstrating new augmented AgentDef fields.

Bundles:
- Custom system prompt (read-only investigation mode)
- Raw tools via the ``tools`` field (no ``register(api)`` needed)
- Tool groups for intra-profile cycling
- Per-profile skill list
"""

from __future__ import annotations

from vtx.agents import AgentDef


def _file_stats(path: str) -> str:
    """Return a summary of a file's structure."""
    # Stub — real implementation would inspect the file contents.
    return f"(stub) file summary for {path!r}"


def _schema_lookup(query: str) -> str:
    """Look up a database schema by name or pattern."""
    return f"(stub) schema result for {query!r}"


AGENT = AgentDef(
    name="data-engineer",
    description="Read-only data investigation profile.",
    icon="🗄️",
    color="green",
    thinking_level="high",
    max_turns=300,
    instructions=(
        "You are a data-engineering specialist. Your job is to investigate "
        "schemas, data quality, pipeline behavior, and query performance. "
        "Be methodical: identify the relevant tables, trace the data lineage, "
        "and surface concrete issues with line references. Do not modify files."
    ),
    instructions_mode="append",
    tools_allow=["read", "find", "grep", "skill", "fetch_webpage", "web_search"],
    tools_deny=["bash", "write", "edit"],
    # New field: raw tools declared directly on AgentDef.
    # These are auto-wrapped into BaseTool instances at load time.
    tools=[_file_stats, _schema_lookup],
    # New field: named tool groups for intra-profile cycling.
    tool_groups={
        "read-only": ["read", "find", "grep", "skill", "fetch_webpage", "web_search"],
        "with-search": ["read", "find", "grep", "skill", "fetch_webpage", "web_search"],
    },
    # New field: per-profile skills. These skill names are matched against
    # loaded skills and the matching descriptions are injected into the
    # system prompt when this agent is active.
    skills=["code-review", "security-audit"],
)
