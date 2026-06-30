"""VTX Claw tool system — restricted tool set for messaging gateway.

This package provides:
- The claw-approved subset of vtx's built-in tools
- The ``mcp`` proxy tool for MCP server discovery and calling
- The claw's own local tool infrastructure

The ``mcp`` subpackage is entirely self-contained and never touches vtx's
global tool registry. It is only available through the claw gateway path.
"""

from vtx.tools import BaseTool, get_tool

# Tools available in VTX Claw (subset of VTX's full tool set)
CLAW_TOOL_NAMES: list[str] = [
    "read",
    "write",
    "edit",
    "bash",
    "fetch_webpage",
    "web_search",
    "skill",
    "mcp",  # MCP proxy tool — only available in claw, never in vtx
]


def get_claw_tools() -> list[BaseTool]:
    """Return only the tools approved for VTX Claw usage."""
    tools: list[BaseTool] = []
    for name in CLAW_TOOL_NAMES:
        if name == "mcp":
            continue  # MCP tool is added separately with its registry
        tool = get_tool(name)
        if tool is not None:
            tools.append(tool)
    return tools
