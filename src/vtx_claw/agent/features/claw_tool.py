"""A claw-specific feature exposed as a real vtx ``@tool``.

This demonstrates the "patch the vtx backend to add a claw feature" pattern:
we build a genuine ``vtx.sdk`` tool (not claw's own ``BaseTool``) and inject
it onto the vtx ``Agent`` via ``clone(tools=...)``. The tool runs on the vtx
agent loop, so it behaves exactly like any other backend tool.
"""

from __future__ import annotations

from pathlib import Path

from vtx.sdk import tool
from vtx_claw.agent.context import ContextBuilder
from vtx_claw.agent.memory import MemoryStore


@tool
def claw_status(workspace: str, detail: str = "brief") -> str:
    """Report claw's workspace and running state.

    Use this to summarize where the agent is currently operating and what
    bootstrap/memory state is present in that workspace.

    Args:
        workspace: Absolute path to the agent's workspace root.
        detail: "brief" for a one-line summary, or "full" for details
            including bootstrap files (SOUL.md/USER.md) and memory size.
    """
    root = Path(workspace).expanduser().resolve()
    builder = ContextBuilder(root)

    identity = builder._get_identity(workspace=root).splitlines()
    platform_line = next((ln for ln in identity if ln.startswith("Runtime")), "")
    summary = f"workspace={root} | {platform_line}"

    if detail != "full":
        return summary

    bootstrap = [name for name in ContextBuilder.BOOTSTRAP_FILES if (root / name).exists()]
    memory = MemoryStore(root)
    mem_text = memory.read_memory()
    lines = [
        summary,
        f"bootstrap_files={bootstrap or 'none'}",
        f"memory_bytes={len(mem_text.encode('utf-8'))}",
    ]
    return "\n".join(lines)


def patch_agent_with_claw_tool(agent: vtx.sdk.Agent, *, tool=None) -> vtx.sdk.Agent:
    """Return a clone of *agent* with the claw tool injected.

    The tool (defaults to :func:`claw_status`) is appended to the agent's
    existing tool list by cloning with a new ``tools`` field, so the
    original agent is left untouched and the feature runs on the vtx loop.
    """
    claw_tool = tool if tool is not None else claw_status
    return agent.clone(tools=[*agent.tools, claw_tool])
