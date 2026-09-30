"""MCP server configuration, read from ``mcp.json``.

Two files, both optional:

1. ``~/.vtx/mcp.json`` -- per-user servers
2. ``<project>/.vtx/mcp.json`` -- per-project servers, read only when the
   project is trusted

Both use the ``mcpServers`` shape shared by other MCP clients, so a config a
user already has for Claude Desktop, Cursor, or another agent copies over
unchanged. Project entries replace global entries with the same name.

Example::

    {
      "mcpServers": {
        "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
        "docs": {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ${DOCS_TOKEN}"}}
      }
    }
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vtx.core.paths import get_config_dir

MCP_CONFIG_FILENAME = "mcp.json"
PROJECT_CONFIG_DIRNAME = ".vtx"

_SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
# $NAME or ${NAME} in a config value.
_ENV_REF = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})


def expand_env(value: str) -> str:
    """Expand ``$NAME`` / ``${NAME}`` from the environment.

    Deliberately not :func:`os.path.expandvars`, which expands a bare
    ``$UNSET`` to empty but leaves ``${UNSET}`` as literal text. An unset
    variable expanding to empty either way is the behaviour that matters: a
    missing optional token should leave the header empty and let the server
    answer 401, not crash the session on a KeyError.
    """
    return _ENV_REF.sub(lambda m: os.environ.get(m.group(1) or m.group(2) or "", ""), value)


@dataclass
class McpOAuthSettings:
    """OAuth client settings for a server that does not support dynamic
    client registration. Without ``client_id`` the flow registers a client
    with the authorization server first."""

    client_id: str | None = None
    client_secret: str | None = None
    callback_port: int | None = None
    scope: str | None = None


@dataclass
class McpServerConfig:
    """One configured server. Exactly one of ``command`` / ``url`` is set."""

    name: str
    source: str = ""
    scope: str = "global"
    enabled: bool = True
    timeout_seconds: int = 60
    # stdio
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    # http
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    oauth: McpOAuthSettings | None = None

    @property
    def is_stdio(self) -> bool:
        return self.command is not None

    def resolved_env(self) -> dict[str, str]:
        """``env`` with ``$VAR`` / ``${VAR}`` references expanded.

        An unset variable expands to empty rather than raising, so a missing
        optional token does not stop a server from starting -- the server will
        answer 401 and be reported as needing auth.
        """
        return {key: expand_env(value) for key, value in self.env.items()}

    def resolved_headers(self) -> dict[str, str]:
        return {key: expand_env(value) for key, value in self.headers.items()}


@dataclass
class LoadedMcpConfig:
    servers: list[McpServerConfig] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def enabled_servers(self) -> list[McpServerConfig]:
        return [s for s in self.servers if s.enabled]

    def get(self, name: str) -> McpServerConfig | None:
        return next((s for s in self.servers if s.name == name), None)


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _is_string_map(value: Any) -> bool:
    return _is_record(value) and all(isinstance(v, str) for v in value.values())


def _validate_oauth(name: str, value: Any) -> tuple[McpOAuthSettings | None, str | None]:
    if value is None:
        return None, None
    if not _is_record(value):
        return None, f'MCP server "{name}": oauth must be an object'
    for key in ("clientId", "clientSecret", "scope"):
        if value.get(key) is not None and not isinstance(value[key], str):
            return None, f'MCP server "{name}": oauth.{key} must be a string'
    port = value.get("callbackPort")
    if port is not None and (not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535):
        return None, f'MCP server "{name}": oauth.callbackPort must be a port number'
    return (
        McpOAuthSettings(
            client_id=value.get("clientId"),
            client_secret=value.get("clientSecret"),
            callback_port=port,
            scope=value.get("scope"),
        ),
        None,
    )


def validate_mcp_server_config(name: str, value: Any) -> tuple[McpServerConfig | None, str | None]:
    """Validate one ``mcpServers`` entry. Returns ``(config, error)``."""
    if not _SERVER_NAME.match(name):
        return None, f"MCP server name {name!r} must match [A-Za-z0-9_-]+"
    if not _is_record(value):
        return None, f'MCP server "{name}": entry must be an object'

    has_command = "command" in value
    has_url = "url" in value
    if has_command == has_url:
        return None, f'MCP server "{name}": set exactly one of "command" or "url"'

    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        return None, f'MCP server "{name}": enabled must be a boolean'

    timeout = value.get("timeout", 60)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        return None, f'MCP server "{name}": timeout must be a positive number of seconds'

    common = {"name": name, "enabled": enabled, "timeout_seconds": int(timeout)}

    if has_command:
        command = value.get("command")
        if not isinstance(command, str) or not command:
            return None, f'MCP server "{name}": command must be a non-empty string'
        args = value.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            return None, f'MCP server "{name}": args must be a list of strings'
        env = value.get("env", {})
        if not _is_string_map(env):
            return None, f'MCP server "{name}": env must be an object of strings'
        cwd = value.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            return None, f'MCP server "{name}": cwd must be a string'
        if "url" in value or "headers" in value or "oauth" in value:
            return None, f'MCP server "{name}": stdio servers take no url/headers/oauth'
        return (
            McpServerConfig(
                **common,
                command=command,
                args=list(args),
                env=dict(env),
                cwd=cwd,
            ),
            None,
        )

    url = value.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None, f'MCP server "{name}": url must be an http(s) URL'
    headers = value.get("headers", {})
    if not _is_string_map(headers):
        return None, f'MCP server "{name}": headers must be an object of strings'
    oauth, oauth_error = _validate_oauth(name, value.get("oauth"))
    if oauth_error:
        return None, oauth_error
    return (
        McpServerConfig(
            **common,
            url=url,
            headers=dict(headers),
            oauth=oauth,
        ),
        None,
    )


def _read_config_file(path: Path, scope: str, servers: dict[str, McpServerConfig], errors: list[str]) -> None:
    if not path.is_file():
        return
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: {exc}")
        return
    if not _is_record(parsed) or (parsed.get("mcpServers") is not None and not _is_record(parsed["mcpServers"])):
        errors.append(f'{path}: expected an object with an "mcpServers" object')
        return

    entries = parsed.get("mcpServers") or {}
    for name, value in entries.items():
        config, error = validate_mcp_server_config(name, value)
        if error:
            errors.append(f"{path}: {error}")
            continue
        assert config is not None
        config.source = str(path)
        config.scope = scope
        # Project entries replace global ones with the same name; they are read
        # second, so a plain assignment is the precedence rule.
        servers[name] = config


def load_mcp_config(*, cwd: str, project_trusted: bool = False, config_dir: Path | None = None) -> LoadedMcpConfig:
    """Load global, then (when trusted) project configuration.

    A disabled server is still returned with ``enabled=False`` so it can be
    re-enabled later without the user retyping it.
    """
    servers: dict[str, McpServerConfig] = {}
    errors: list[str] = []
    base = config_dir or get_config_dir()

    _read_config_file(base / MCP_CONFIG_FILENAME, "global", servers, errors)
    if project_trusted:
        _read_config_file(
            Path(cwd) / PROJECT_CONFIG_DIRNAME / MCP_CONFIG_FILENAME, "project", servers, errors
        )
    return LoadedMcpConfig(servers=list(servers.values()), errors=errors)


def update_mcp_server_config(path: Path, name: str, patch: dict[str, Any]) -> None:
    """Apply ``patch`` to one server in ``mcp.json``, creating the file if needed.

    Other content in the file is preserved. ``enabled: True`` removes the key
    rather than writing it, so a file never accumulates explicit defaults.
    """
    parsed: dict[str, Any] = {}
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not _is_record(loaded):
            raise ValueError(f"{path}: expected a JSON object")
        parsed = loaded
    entries = parsed.get("mcpServers")
    if entries is None:
        entries = {}
    if not _is_record(entries):
        raise ValueError(f'{path}: expected an "mcpServers" object')
    if name not in entries:
        raise ValueError(f'{path} does not define MCP server "{name}"')

    server = dict(entries[name])
    for key, value in patch.items():
        if key == "enabled":
            if value:
                server.pop("enabled", None)
            else:
                server["enabled"] = False
        else:
            server[key] = value
    entries[name] = server
    parsed["mcpServers"] = entries
    write_mcp_config(path, parsed)


def add_mcp_server_config(path: Path, name: str, config: McpServerConfig) -> None:
    parsed: dict[str, Any] = {}
    if path.is_file():
        parsed = json.loads(path.read_text(encoding="utf-8"))
    entries = parsed.get("mcpServers")
    if not _is_record(entries):
        entries = {}
    entries[name] = {
        k: v
        for k, v in (
            ("command", config.command),
            ("args", config.args or None),
            ("env", config.env or None),
            ("cwd", config.cwd),
            ("url", config.url),
            ("headers", config.headers or None),
        )
        if v is not None
    }
    parsed["mcpServers"] = entries
    write_mcp_config(path, parsed)


def remove_mcp_server_config(path: Path, name: str) -> bool:
    if not path.is_file():
        return False
    parsed = json.loads(path.read_text(encoding="utf-8"))
    entries = parsed.get("mcpServers")
    if not _is_record(entries) or name not in entries:
        return False
    del entries[name]
    parsed["mcpServers"] = entries
    write_mcp_config(path, parsed)
    return True


def write_mcp_config(path: Path, parsed: dict[str, Any]) -> None:
    """Write atomically, matching the indentation the file already used."""
    indent = 2
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^([ \t]+)\S", text, re.MULTILINE)
        if match:
            indent = len(match.group(1))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(parsed, indent=indent) + "\n", encoding="utf-8")
    os.replace(tmp, path)


__all__ = [
    "LOOPBACK_HOSTS",
    "MCP_CONFIG_FILENAME",
    "LoadedMcpConfig",
    "McpOAuthSettings",
    "McpServerConfig",
    "add_mcp_server_config",
    "expand_env",
    "load_mcp_config",
    "remove_mcp_server_config",
    "update_mcp_server_config",
    "validate_mcp_server_config",
    "write_mcp_config",
]
