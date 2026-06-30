"""vtx_claw MCP tool system — self-contained MCP integration for the claw gateway.

This package is entirely local to vtx_claw. Nothing here is added to vtx's
``tools_by_name`` or global tool list. MCP tools are only available through
the claw gateway's tool surface via the ``mcp`` proxy tool.

Components:
- config: Server config, tool specs, config file loading
- capabilities: Capability negotiation, resource/prompt models
- cache: Persistent JSON metadata cache for offline tool discovery
- client: MCPClient — async MCP SDK wrapper (connect, call, list)
- lifecycle: Lifecycle manager for lazy/eager/keep-alive server connections
- registry: MCPRegistry — manages multiple servers
- proxy_tool: MCPProxyTool — the single vtx BaseTool exposed to the LLM
"""

from vtx_claw.tools.mcp.config import MCPServerConfig
from vtx_claw.tools.mcp.proxy_tool import MCPProxyTool
from vtx_claw.tools.mcp.registry import MCPRegistry, create_mcp_registry

__all__ = ["MCPProxyTool", "MCPRegistry", "MCPServerConfig", "create_mcp_registry"]
