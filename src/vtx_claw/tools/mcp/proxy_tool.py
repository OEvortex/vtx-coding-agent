"""MCP Proxy Tool — single vtx-style BaseTool for all MCP servers.

Instead of registering dozens of MCP tools, only one proxy tool is added to the
claw tool list (~200 tokens). The LLM selects a mode by providing the relevant
parameter.

Modes (in precedence order):
1. call:   tool="tool_name" args='{"key":"val"}' [server="server_name"]
2. connect: connect="server_name"
3. describe: describe="tool_name" [server="server_name"]
4. search: search="query" [server="name"]
5. list:   server="server_name"
6. resources: resources="server_name"
7. prompts: prompts="server_name"
8. status: (no args)

Adapted from Jarvis's MCPProxyTool.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field
from vtx.core.types import ToolResult
from vtx.tools.base import BaseTool

from vtx_claw.tools.mcp.cache import ToolMetadata

if True:  # TYPE_CHECKING workaround for circular import
    from vtx_claw.tools.mcp.registry import MCPRegistry

logger = logging.getLogger(__name__)


class MCPParams(BaseModel):
    """Parameters for the MCP proxy tool."""

    tool: str | None = Field(
        default=None,
        description=(
            "Tool name to call (triggers call mode). "
            "Use the full prefixed name like 'mcp_server_toolname'."
        ),
    )
    args: str | None = Field(
        default=None, description="JSON string of arguments for the tool call."
    )
    server: str | None = Field(
        default=None,
        description="Server name to filter by, target for listing, or connect target.",
    )
    search: str | None = Field(
        default=None, description="Natural language search query to find tools via fuzzy matching."
    )
    describe: str | None = Field(
        default=None, description="Tool name to describe (shows full schema)."
    )
    connect: str | None = Field(
        default=None,
        description=(
            "Server name to explicitly connect (triggers lazy connect + metadata refresh)."
        ),
    )
    resources: str | None = Field(default=None, description="Server name to list resources from.")
    prompts: str | None = Field(default=None, description="Server name to list prompts from.")
    status: bool | None = Field(
        default=None, description="Show status of all configured MCP servers."
    )


class MCPProxyTool(BaseTool[MCPParams]):
    """Single proxy tool for all MCP servers — token-efficient interface.

    Use this proxy tool to discover, describe, and call tools, list resources,
    render prompts, or check the status of any configured MCP server.
    """

    name = "mcp"
    description = (
        "Discover and call tools/resources/prompts from MCP servers. "
        "Use mcp(search='natural language query') to find tools by description; "
        "mcp(server='name') to list a server's tools; "
        "mcp(tool='name' args='{...}') to call a tool; "
        "mcp(status) to list servers and their status. "
        "MCP servers are configured in ~/.vtx/claw/mcp.yml."
    )
    params = MCPParams
    mutating = True
    tool_icon = "🔌"
    prompt_guidelines = (
        "MCP tools are prefixed with mcp_<server>_<name>. "
        "Use mcp(search='...') to find the right tool.",
    )

    def __init__(self, mcp_registry: MCPRegistry) -> None:
        self._mcp_registry = mcp_registry
        self._synced_names: frozenset[str] = frozenset()
        super().__init__()

    def _sync_cache(self) -> None:
        """Sync knowledge of cached tools (no embedding index in vtx)."""
        cache = self._mcp_registry.cache
        all_tools = cache.list_all_tools()
        current_names = frozenset(t.name for t in all_tools)
        self._synced_names = current_names

    # -- Formatting helpers --------------------------------------------------

    def _format_parameters(self, schema: dict[str, Any]) -> list[str]:
        """Format parameters into readable list."""
        lines: list[str] = []
        props = schema.get("properties", {})
        required = schema.get("required", [])
        for pname, pdef in props.items():
            req_marker = "required" if pname in required else "optional"
            ptype = pdef.get("type", "any")
            pdesc = pdef.get("description", "")
            lines.append(f"    - `{pname}` ({ptype}, {req_marker}): {pdesc}")
        return lines

    def _format_tool_result(self, tool: ToolMetadata, include_schemas: bool = True) -> str:
        """Format a single MCP tool into readable output."""
        lines: list[str] = []
        lines.append(f"### {tool.name}")
        desc = tool.description
        if len(desc) > 150:
            desc = desc[:147] + "..."
        lines.append(f"**Description**: {desc}")
        lines.append(f"**Server**: {tool.server_name}")
        lines.append(f"**Original name**: {tool.original_name}\n")
        schema = tool.input_schema
        if schema and include_schemas:
            params = self._format_parameters(schema)
            if params:
                lines.append("**Key parameters**:")
                lines.extend(params)
                lines.append("")
        return "\n".join(lines)

    # -- Execute -------------------------------------------------------------

    async def execute(self, params: MCPParams, cancel_event: Any = None) -> ToolResult:
        """Route to appropriate mode handler based on provided parameters."""
        try:
            if params.tool:
                result_text = await self._handle_call(
                    tool_name=params.tool, args_str=params.args or "{}", server=params.server
                )
            elif params.connect:
                result_text = await self._handle_connect(params.connect)
            elif params.describe:
                result_text = await self._handle_describe(params.describe, params.server)
            elif params.search:
                result_text = await self._handle_search(query=params.search, server=params.server)
            elif params.resources:
                result_text = await self._handle_resources(params.resources)
            elif params.prompts:
                result_text = await self._handle_prompts(params.prompts)
            elif params.server:
                result_text = await self._handle_list(params.server)
            else:
                result_text = await self._handle_status()

            return ToolResult(
                success=True, result=result_text, ui_summary=f"MCP: {len(result_text)} chars"
            )

        except Exception as e:
            logger.error("MCP proxy tool error: %s", e)
            return ToolResult(success=False, result=f"MCP proxy error: {e}")

    # -- Mode: status --------------------------------------------------------

    async def _handle_status(self) -> str:
        """Show status of all configured MCP servers."""
        registry = self._mcp_registry
        cache = registry.cache

        lines = ["## MCP Server Status\n"]

        for server_name in sorted(registry._configs.keys()):
            client = registry.get_client(server_name)
            connected = client.is_connected if client else False
            connected_str = "connected" if connected else "disconnected"

            tool_count = 0
            resource_count = 0
            prompt_count = 0
            cached_smeta = cache.get_server(server_name)
            if cached_smeta:
                tool_count = len(cached_smeta.tools)
                resource_count = len(cached_smeta.resources)
                prompt_count = len(cached_smeta.prompts)

            config = registry._configs[server_name]
            lifecycle_mode = config.lifecycle

            caps_parts: list[str] = []
            if tool_count:
                caps_parts.append(f"{tool_count}t")
            if resource_count:
                caps_parts.append(f"{resource_count}r")
            if prompt_count:
                caps_parts.append(f"{prompt_count}p")
            caps_badge = f"[{','.join(caps_parts)}]" if caps_parts else ""

            auth_badge = ""
            if config.auth and config.auth.is_configured:
                auth_badge = f" auth={config.auth.type}"

            lines.append(
                f"  **{server_name}** {caps_badge}{auth_badge} — {lifecycle_mode}, {connected_str}"
            )

        total_cached = cache.total_tools
        total_resources = cache.total_resources
        total_prompts = cache.total_prompts
        summary_parts = [f"{total_cached} tools"]
        if total_resources:
            summary_parts.append(f"{total_resources} resources")
        if total_prompts:
            summary_parts.append(f"{total_prompts} prompts")

        lines.append(
            f"\n  Total: {len(registry._configs)} servers, "
            f"{registry.connected_servers} connected, "
            f"{', '.join(summary_parts)} in cache"
        )

        return "\n".join(lines)

    # -- Mode: list ----------------------------------------------------------

    async def _handle_list(self, server: str) -> str:
        """List tools for a specific server."""
        cache = self._mcp_registry.cache
        client = self._mcp_registry.get_client(server)

        cached_tools = cache.list_server_tools(server)
        if cached_tools:
            lines = [f"## Tools from '{server}'\n"]
            for i, tool in enumerate(cached_tools, 1):
                lines.append(f"{i}. {self._format_tool_result(tool, include_schemas=False)}")
                lines.append("---\n")
            lines.append(f"{len(cached_tools)} tools")
            return "\n".join(lines)

        if client and client.is_connected:
            tools = await client.list_tools()
            lines = [f"## Tools from '{server}'\n"]
            for i, tool in enumerate(tools, 1):
                desc = (
                    tool.description[:150] + "..."
                    if len(tool.description) > 150
                    else tool.description
                )
                lines.append(f"{i}. **{tool.name}**\n**Description**: {desc}\n")
                lines.append("---\n")
            lines.append(f"\n  {len(tools)} tools")
            return "\n".join(lines)

        return (
            f"Server '{server}' not found in cache or not connected. "
            f"Use mcp(connect='{server}') first."
        )

    # -- Mode: search --------------------------------------------------------

    async def _handle_search(self, query: str, server: str | None = None) -> str:
        """Search for tools by name/description."""
        self._sync_cache()
        cache = self._mcp_registry.cache

        all_tools = cache.list_all_tools()
        if server:
            all_tools = [t for t in all_tools if t.server_name == server]

        if not all_tools:
            return "No tools available" + (f" on server '{server}'" if server else "")

        # Use the cache's built-in search
        matches = cache.search_tools(query, server=server)

        if not matches:
            return f"No tools matching '{query}'"

        lines = [f"## Search results for '{query}'\n"]
        for i, result in enumerate(matches, 1):
            lines.append(f"{i}. {self._format_tool_result(result)}")
            lines.append("---\n")

        lines.append(f"{len(matches)} match(es) | {len(all_tools)} MCP tools available")
        return "\n".join(lines)

    # -- Mode: describe ------------------------------------------------------

    async def _handle_describe(self, tool_name: str, server: str | None = None) -> str:
        """Describe a specific tool with full schema."""
        cache = self._mcp_registry.cache

        tool_meta = cache.get_tool_by_prefixed_name(tool_name)
        if not tool_meta and server:
            tool_meta = cache.get_tool(server, tool_name)

        if not tool_meta:
            all_tools = cache.search_tools(tool_name)
            if all_tools:
                tool_meta = all_tools[0]

        if not tool_meta:
            return f"Tool '{tool_name}' not found. Use mcp(search='{tool_name}') to find it."

        return self._format_tool_result(tool_meta, include_schemas=True)

    # -- Mode: call ----------------------------------------------------------

    async def _handle_call(
        self, tool_name: str, args_str: str = "{}", server: str | None = None
    ) -> str:
        """Execute an MCP tool call."""
        try:
            args = json.loads(args_str) if isinstance(args_str, str) else args_str
        except json.JSONDecodeError as e:
            return f"Invalid JSON args: {e}"

        # Resolve server if not specified
        if not server:
            cache = self._mcp_registry.cache
            tool_meta = cache.get_tool_by_prefixed_name(tool_name)
            if tool_meta:
                server = tool_meta.server_name
                tool_name = tool_meta.original_name
            else:
                for sname in self._mcp_registry._configs:
                    meta = cache.get_tool(sname, tool_name)
                    if meta:
                        server = sname
                        tool_name = meta.original_name
                        break

        if not server:
            return f"Could not determine server for tool '{tool_name}'. Specify server='name'."

        # Ensure server is connected
        try:
            client = await self._mcp_registry.lifecycle.ensure_connected(server)
        except Exception as e:
            return f"Failed to connect to MCP server '{server}': {e}"

        try:
            result = await client.call_tool(tool_name, args)
            await self._mcp_registry.lifecycle.on_tool_call(server)

            content = result.get("content", [])
            is_error = result.get("isError", False)

            if isinstance(content, list):
                text_parts: list[str] = []
                for item in content:
                    if isinstance(item, dict):
                        text_parts.append(item.get("text", str(item)))
                    else:
                        text_parts.append(str(item))
                text = "\n".join(text_parts)
            else:
                text = str(content) if content else ""

            if is_error:
                return f"MCP tool error: {text}"
            return text

        except Exception as e:
            return f"MCP tool call failed: {e}"

    # -- Mode: connect -------------------------------------------------------

    async def _handle_connect(self, server: str) -> str:
        """Explicitly connect to a lazy server and refresh its metadata."""
        try:
            client = await self._mcp_registry.lifecycle.ensure_connected(server)
            tools = await client.list_tools()
            capabilities = client.get_capabilities()
            resources = await client.list_resources() if capabilities.resources else []
            prompts = await client.list_prompts() if capabilities.prompts else []
            self._mcp_registry.update_cache_for_server(
                server, tools, resources=resources, prompts=prompts
            )

            caps_info: list[str] = []
            if capabilities.tools:
                caps_info.append(f"{client.tool_count} tools")
            if capabilities.resources:
                caps_info.append(f"{len(resources)} resources")
            if capabilities.prompts:
                caps_info.append(f"{len(prompts)} prompts")

            return f"Connected to '{server}' with {', '.join(caps_info) or 'no capabilities'}"
        except Exception as e:
            return f"Failed to connect to MCP server '{server}': {e}"

    # -- Mode: resources -----------------------------------------------------

    async def _handle_resources(self, server: str) -> str:
        """List resources for a specific server."""
        cache = self._mcp_registry.cache
        client = self._mcp_registry.get_client(server)

        cached_resources = cache.list_server_resources(server)
        if cached_resources:
            lines = [f"## Resources from '{server}'\n"]
            for resource in cached_resources:
                mime = f" ({resource.mime_type})" if resource.mime_type else ""
                lines.append(
                    f"  - **{resource.name}** [{resource.uri}]{mime}: {resource.description}"
                )
            lines.append(f"\n  {len(cached_resources)} resources")
            return "\n".join(lines)

        if client and client.is_connected:
            try:
                resources = await client.list_resources()
                lines = [f"## Resources from '{server}'\n"]
                for resource in resources:
                    mime = f" ({resource.mime_type})" if resource.mime_type else ""
                    lines.append(
                        f"  - **{resource.name}** [{resource.uri}]{mime}: {resource.description}"
                    )
                lines.append(f"\n  {len(resources)} resources")
                return "\n".join(lines)
            except Exception as e:
                return f"Failed to list resources from '{server}': {e}"

        return (
            f"Server '{server}' not found in cache or not connected. "
            f"Use mcp(connect='{server}') first."
        )

    # -- Mode: prompts -------------------------------------------------------

    async def _handle_prompts(self, server: str) -> str:
        """List prompts for a specific server."""
        cache = self._mcp_registry.cache
        client = self._mcp_registry.get_client(server)

        cached_prompts = cache.list_server_prompts(server)
        if cached_prompts:
            lines = [f"## Prompts from '{server}'\n"]
            for prompt in cached_prompts:
                arg_desc = ""
                if prompt.arguments:
                    arg_names = [a.get("name", "?") for a in prompt.arguments]
                    arg_desc = f" (args: {', '.join(arg_names)})"
                lines.append(f"  - **{prompt.name}**{arg_desc}: {prompt.description}")
            lines.append(f"\n  {len(cached_prompts)} prompts")
            return "\n".join(lines)

        if client and client.is_connected:
            try:
                prompts = await client.list_prompts()
                lines = [f"## Prompts from '{server}'\n"]
                for prompt in prompts:
                    arg_desc = ""
                    if prompt.arguments:
                        arg_names = [a.name for a in prompt.arguments]
                        arg_desc = f" (args: {', '.join(arg_names)})"
                    lines.append(f"  - **{prompt.name}**{arg_desc}: {prompt.description}")
                lines.append(f"\n  {len(prompts)} prompts")
                return "\n".join(lines)
            except Exception as e:
                return f"Failed to list prompts from '{server}': {e}"

        return (
            f"Server '{server}' not found in cache or not connected. "
            f"Use mcp(connect='{server}') first."
        )
