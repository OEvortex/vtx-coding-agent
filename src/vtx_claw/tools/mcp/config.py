"""MCP server configuration data models for vtx_claw.

MCP (Model Context Protocol) transport and server config.
Adapted from Jarvis's MCP integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class MCPTransportType:
    """MCP transport types"""

    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


@dataclass
class MCPAuthConfig:
    """Authentication configuration for an MCP server.

    Supported types:
    - "bearer": Static bearer token in Authorization header
    - "api_key": API key in a configurable header
    """

    type: str = ""  # "bearer" | "api_key" | ""
    token: str = ""
    header_name: str = "Authorization"
    header_prefix: str = "Bearer"
    token_env_var: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MCPAuthConfig:
        return cls(
            type=data.get("type", ""),
            token=data.get("token", ""),
            header_name=data.get("headerName", data.get("header_name", "Authorization")),
            header_prefix=data.get("headerPrefix", data.get("header_prefix", "Bearer")),
            token_env_var=data.get("tokenEnvVar", data.get("token_env_var", "")),
        )

    def get_token(self) -> str:
        import os

        if self.token_env_var:
            env_token = os.environ.get(self.token_env_var, "")
            if env_token:
                return env_token
        return self.token

    @property
    def is_configured(self) -> bool:
        return bool(self.type)


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server"""

    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = MCPTransportType.STDIO
    url: str = ""
    timeout: float = 30.0
    disabled: bool = False
    disabled_tools: list[str] = field(default_factory=list)
    lifecycle: str = "lazy"  # "lazy" | "eager" | "keep-alive"
    idle_timeout: float = 15.0  # Minutes before idle disconnect (lazy only)
    exclude_tools: list[str] = field(default_factory=list)
    auto_discover_capabilities: bool = True
    auth: MCPAuthConfig | None = None

    @classmethod
    def from_dict(cls, data: dict) -> MCPServerConfig:
        auth_data = data.get("auth")
        auth = MCPAuthConfig.from_dict(auth_data) if auth_data else None
        return cls(
            name=data.get("name", ""),
            command=data.get("command", ""),
            args=data.get("args", []),
            env=data.get("env", {}),
            transport=data.get("transport", MCPTransportType.STDIO),
            url=data.get("url", ""),
            timeout=data.get("timeout", 30.0),
            disabled=data.get("disabled", False),
            disabled_tools=data.get("disabled_tools", []),
            lifecycle=data.get("lifecycle", "lazy"),
            idle_timeout=data.get("idleTimeout", data.get("idle_timeout", 15.0)),
            exclude_tools=data.get("excludeTools", data.get("exclude_tools", [])),
            auto_discover_capabilities=data.get(
                "autoDiscoverCapabilities", data.get("auto_discover_capabilities", True)
            ),
            auth=auth,
        )


@dataclass
class MCPToolSpec:
    """Specification for an MCP tool"""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str = ""
    remote_name: str = ""


def load_mcp_configs(config_path: str) -> list[MCPServerConfig]:
    """Load MCP server configurations from a JSON or YAML file."""
    from pathlib import Path

    path = Path(config_path)
    if not path.exists():
        return []

    content = path.read_text()

    if path.suffix == ".json":
        import json

        data = json.loads(content)
    elif path.suffix in (".yaml", ".yml"):
        try:
            import yaml

            data = yaml.safe_load(content)
        except ImportError:
            raise RuntimeError("PyYAML is required for YAML config files") from None
    else:
        raise ValueError(f"Unsupported config file format: {path.suffix}")

    if isinstance(data, dict):
        if "mcpServers" in data:
            servers = list(data["mcpServers"].values())
        elif "mcp_servers" in data:
            servers = data["mcp_servers"]
        elif "servers" in data:
            servers = data["servers"]
        else:
            servers = [data]
    else:
        servers = data

    return [MCPServerConfig.from_dict(s) for s in servers]
