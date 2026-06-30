"""MCP Registry for vtx_claw — manages multiple MCP servers with lifecycle support.

Provides a centralized registry for connecting, configuring, and managing
MCP servers. Supports lazy/eager/keep-alive lifecycle modes and metadata caching.

Adapted from Jarvis's MCPRegistry.
"""

from __future__ import annotations

import logging
from typing import Any

from vtx_claw.tools.mcp.cache import (
    MCPMetadataCache,
    PromptMetadata,
    ResourceMetadata,
    ToolMetadata,
)
from vtx_claw.tools.mcp.client import MCPClient
from vtx_claw.tools.mcp.config import MCPServerConfig, MCPToolSpec
from vtx_claw.tools.mcp.lifecycle import MCPLifecycleManager

logger = logging.getLogger(__name__)


class MCPRegistry:
    """Registry for managing multiple MCP servers with lazy/eager/keep-alive lifecycle."""

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._configs: dict[str, MCPServerConfig] = {}
        self._cache = MCPMetadataCache()
        self._lifecycle = MCPLifecycleManager(self)
        self._initialized = False

    async def initialize(self, configs: list[MCPServerConfig]) -> dict[str, str]:
        """Initialize all MCP servers based on their lifecycle mode.

        - eager/keep-alive: connect immediately
        - lazy: register from metadata cache only

        Returns:
            Dict mapping server names to their initialization status.
        """
        results: dict[str, str] = {}

        for config in configs:
            if config.disabled:
                results[config.name] = "disabled"
                continue
            self._configs[config.name] = config
            try:
                await self._lifecycle.initialize_server(config)
                results[config.name] = "initialized"
            except Exception as e:
                results[config.name] = f"error: {e}"
                logger.error("Failed to initialize MCP server '%s': %s", config.name, e)

        self._initialized = True
        return results

    def get_client(self, server_name: str) -> MCPClient | None:
        """Get the MCPClient for a server."""
        return self._clients.get(server_name)

    async def remove_server(self, server_name: str) -> None:
        """Remove an MCP server and its tools."""
        await self._lifecycle.disconnect_server(server_name)
        self._configs.pop(server_name, None)
        self._cache.remove_server(server_name)

    async def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        await self._lifecycle.shutdown()
        self._clients.clear()
        self._configs.clear()

    def list_servers(self) -> list[str]:
        """List all configured MCP server names."""
        return list(self._configs.keys())

    def list_tools(self) -> list[dict[str, Any]]:
        """List all MCP tools (from cache)."""
        tool_list: list[dict[str, Any]] = []
        for cached_tool in self._cache.list_all_tools():
            tool_list.append(
                {
                    "name": cached_tool.name,
                    "description": cached_tool.description,
                    "server": cached_tool.server_name,
                    "remote_name": cached_tool.original_name,
                }
            )
        return tool_list

    @property
    def total_tools(self) -> int:
        """Get the total number of MCP tools."""
        return self._cache.total_tools

    @property
    def connected_servers(self) -> int:
        """Get the number of connected servers."""
        return sum(1 for c in self._clients.values() if c.is_connected)

    @property
    def cache(self) -> MCPMetadataCache:
        """Get the metadata cache."""
        return self._cache

    @property
    def lifecycle(self) -> MCPLifecycleManager:
        """Get the lifecycle manager."""
        return self._lifecycle

    # -- Events for the proxy tool ------------------------------------------

    def get_config_dict(self, server_name: str) -> dict[str, Any]:
        """Get config dict for cache validation."""
        config = self._configs.get(server_name)
        if not config:
            return {}
        return {
            "command": config.command,
            "args": config.args,
            "url": config.url,
            "transport": config.transport,
            "env": config.env,
        }

    def update_cache_for_server(
        self,
        server_name: str,
        tools: list[MCPToolSpec],
        resources: list | None = None,
        prompts: list | None = None,
    ) -> None:
        """Update the metadata cache with fresh tool, resource, and prompt data."""
        config_dict = self.get_config_dict(server_name)

        tool_metas = [
            ToolMetadata(
                name=f"mcp_{server_name}_{t.name}",
                original_name=t.name,
                description=t.description,
                input_schema=t.input_schema,
                server_name=server_name,
            )
            for t in tools
        ]

        resource_metas = [
            ResourceMetadata(
                uri=r.uri,
                name=r.name,
                description=r.description,
                mime_type=r.mime_type,
                server_name=server_name,
            )
            for r in (resources or [])
        ]

        prompt_metas = [
            PromptMetadata(
                name=p.name,
                description=p.description,
                arguments=[
                    {"name": a.name, "description": a.description, "required": a.required}
                    for a in p.arguments
                ],
                server_name=server_name,
            )
            for p in (prompts or [])
        ]

        self._cache.update_server(
            server_name, tool_metas, config_dict, resources=resource_metas, prompts=prompt_metas
        )


async def create_mcp_registry(config_path: str | None = None) -> MCPRegistry:
    """Create and initialize an MCP registry from an optional config file.

    Args:
        config_path: Path to JSON/YAML config file.
            Defaults to ``~/.vtx/claw/mcp.yml`` and then ``./.mcp.json``.

    Returns:
        Initialized MCPRegistry.
    """
    from pathlib import Path

    from vtx_claw.tools.mcp.config import load_mcp_configs

    if not config_path:
        # Try default locations
        home_path = Path.home() / ".vtx" / "claw" / "mcp.yml"
        cwd_path = Path.cwd() / ".mcp.json"
        if home_path.exists():
            config_path = str(home_path)
        elif cwd_path.exists():
            config_path = str(cwd_path)
        else:
            config_path = ""

    configs = load_mcp_configs(config_path) if config_path else []

    registry = MCPRegistry()
    if configs:
        await registry.initialize(configs)
    else:
        logger.info("No MCP configuration found, MCP subsystem will be inactive")

    return registry
