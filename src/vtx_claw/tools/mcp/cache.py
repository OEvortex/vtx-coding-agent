"""MCP Metadata Cache for lazy tool discovery in vtx_claw.

Stores tool definitions from MCP servers in a local JSON cache so tools
can be discovered/searched without live server connections.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".vtx" / "claw"
CACHE_FILE = CACHE_DIR / "mcp-cache.json"


@dataclass
class ToolMetadata:
    """Metadata for a single MCP tool."""

    name: str  # Prefixed name: "mcp_servername_toolname"
    original_name: str  # Original MCP tool name
    description: str
    input_schema: dict[str, Any]
    server_name: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolMetadata:
        return cls(
            name=data["name"],
            original_name=data["original_name"],
            description=data.get("description", ""),
            input_schema=data.get("input_schema", {}),
            server_name=data.get("server_name", ""),
        )


@dataclass
class ResourceMetadata:
    """Metadata for an MCP resource."""

    uri: str
    name: str
    description: str
    mime_type: str
    server_name: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceMetadata:
        return cls(
            uri=data["uri"],
            name=data.get("name", ""),
            description=data.get("description", ""),
            mime_type=data.get("mime_type", ""),
            server_name=data.get("server_name", ""),
        )


@dataclass
class PromptMetadata:
    """Metadata for an MCP prompt template."""

    name: str
    description: str
    arguments: list[dict[str, Any]]
    server_name: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromptMetadata:
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            arguments=data.get("arguments", []),
            server_name=data.get("server_name", ""),
        )


@dataclass
class ServerMetadata:
    """Metadata for an MCP server including its tools, resources, and prompts."""

    name: str
    tools: list[ToolMetadata]
    resources: list[ResourceMetadata]
    prompts: list[PromptMetadata]
    cached_at: float
    config_hash: str  # Hash of server config for validation

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tools": [t.to_dict() for t in self.tools],
            "resources": [r.to_dict() for r in self.resources],
            "prompts": [p.to_dict() for p in self.prompts],
            "cached_at": self.cached_at,
            "config_hash": self.config_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServerMetadata:
        return cls(
            name=data["name"],
            tools=[ToolMetadata.from_dict(t) for t in data.get("tools", [])],
            resources=[ResourceMetadata.from_dict(r) for r in data.get("resources", [])],
            prompts=[PromptMetadata.from_dict(p) for p in data.get("prompts", [])],
            cached_at=data.get("cached_at", 0.0),
            config_hash=data.get("config_hash", ""),
        )


def compute_config_hash(config_dict: dict[str, Any]) -> str:
    """Compute a hash of relevant server config fields for cache validation."""
    relevant_keys = ["command", "args", "url", "transport", "env"]
    relevant = {k: config_dict.get(k) for k in relevant_keys if k in config_dict}
    raw = json.dumps(relevant, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class MCPMetadataCache:
    """Persistent cache of MCP tool metadata for discovery without live connections."""

    def __init__(self, cache_path: Path | None = None) -> None:
        self._cache_path = cache_path or CACHE_FILE
        self._servers: dict[str, ServerMetadata] = {}
        self._load()

    def _load(self) -> None:
        """Load cache from disk."""
        if not self._cache_path.exists():
            self._servers = {}
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            self._servers = {
                name: ServerMetadata.from_dict(sdata)
                for name, sdata in data.get("servers", {}).items()
            }
        except Exception as e:
            logger.warning(f"Failed to load MCP metadata cache: {e}")
            self._servers = {}

    def save(self) -> None:
        """Save cache to disk."""
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "servers": {name: smeta.to_dict() for name, smeta in self._servers.items()},
                "version": 1,
            }
            self._cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save MCP metadata cache: {e}")

    def get_server(self, name: str) -> ServerMetadata | None:
        """Get metadata for a server."""
        return self._servers.get(name)

    def get_tool(self, server: str, tool_name: str) -> ToolMetadata | None:
        """Get a specific tool's metadata."""
        smeta = self._servers.get(server)
        if not smeta:
            return None
        for tool in smeta.tools:
            if tool.original_name == tool_name or tool.name == tool_name:
                return tool
        return None

    def get_tool_by_prefixed_name(self, prefixed_name: str) -> ToolMetadata | None:
        """Get a tool by its prefixed name (e.g. 'mcp_server_tool')."""
        for smeta in self._servers.values():
            for tool in smeta.tools:
                if tool.name == prefixed_name:
                    return tool
        return None

    def search_tools(self, query: str, server: str | None = None) -> list[ToolMetadata]:
        """Search for tools by name/description."""
        matches: list[ToolMetadata] = []
        servers_to_search = (
            {server: self._servers[server]}
            if server and server in self._servers
            else self._servers
        )

        words = [w for w in query.split() if w]
        patterns = [re.compile(re.escape(w), re.IGNORECASE) for w in words] if words else []

        for smeta in servers_to_search.values():
            for tool in smeta.tools:
                search_text = f"{tool.name} {tool.description} {tool.original_name}"
                if all(p.search(search_text) for p in patterns):
                    matches.append(tool)

        return matches

    def list_server_tools(self, server: str) -> list[ToolMetadata]:
        """List all tools for a server."""
        smeta = self._servers.get(server)
        return smeta.tools if smeta else []

    def list_all_tools(self) -> list[ToolMetadata]:
        """List all tools across all servers."""
        tools: list[ToolMetadata] = []
        for smeta in self._servers.values():
            tools.extend(smeta.tools)
        return tools

    def update_server(
        self,
        name: str,
        tools: list[ToolMetadata],
        config_dict: dict[str, Any],
        resources: list[ResourceMetadata] | None = None,
        prompts: list[PromptMetadata] | None = None,
    ) -> None:
        """Update cache for a server with fresh metadata."""
        existing = self._servers.get(name)
        existing_resources = existing.resources if existing else []
        existing_prompts = existing.prompts if existing else []

        self._servers[name] = ServerMetadata(
            name=name,
            tools=tools,
            resources=resources if resources is not None else existing_resources,
            prompts=prompts if prompts is not None else existing_prompts,
            cached_at=time.time(),
            config_hash=compute_config_hash(config_dict),
        )
        self.save()

    def remove_server(self, name: str) -> None:
        """Remove a server's cache entry."""
        self._servers.pop(name, None)
        self.save()

    @property
    def server_names(self) -> list[str]:
        """Get all cached server names."""
        return list(self._servers.keys())

    @property
    def total_tools(self) -> int:
        """Get total number of cached tools."""
        return sum(len(s.tools) for s in self._servers.values())

    @property
    def total_resources(self) -> int:
        """Get total number of cached resources."""
        return sum(len(s.resources) for s in self._servers.values())

    @property
    def total_prompts(self) -> int:
        """Get total number of cached prompts."""
        return sum(len(s.prompts) for s in self._servers.values())

    # -- Resource cache methods --

    def list_server_resources(self, server: str) -> list[ResourceMetadata]:
        """List all resources for a server."""
        smeta = self._servers.get(server)
        return smeta.resources if smeta else []

    def list_all_resources(self) -> list[ResourceMetadata]:
        """List all resources across all servers."""
        resources: list[ResourceMetadata] = []
        for smeta in self._servers.values():
            resources.extend(smeta.resources)
        return resources

    # -- Prompt cache methods --

    def get_prompt(self, server: str, prompt_name: str) -> PromptMetadata | None:
        """Get a specific prompt's metadata."""
        smeta = self._servers.get(server)
        if not smeta:
            return None
        for prompt in smeta.prompts:
            if prompt.name == prompt_name:
                return prompt
        return None

    def list_server_prompts(self, server: str) -> list[PromptMetadata]:
        """List all prompts for a server."""
        smeta = self._servers.get(server)
        return smeta.prompts if smeta else []

    def list_all_prompts(self) -> list[PromptMetadata]:
        """List all prompts across all servers."""
        prompts: list[PromptMetadata] = []
        for smeta in self._servers.values():
            prompts.extend(smeta.prompts)
        return prompts
