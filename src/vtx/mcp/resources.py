"""MCP resources, reachable by the model.

The client can list and read resources, but without a tool the model cannot ask
for one -- so a server that publishes a database schema or a set of files is
invisible. These are the three session-level tools that close that gap:

    list_mcp_resources
    list_mcp_resource_templates
    read_mcp_resource

They are session-level rather than per-server because the names are the ones
models already know from other coding agents, and because three tools cost the
same however many servers are connected. Each takes a ``server`` argument and
covers every connected server that offers resources.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from vtx.ai.agent.tools.base import BaseTool
from vtx.ai.agent.tools.schema import json_schema_to_pydantic
from vtx.core.types import ToolResult

from .client import McpRequestOptions
from .content import split_content, to_tool_content
from .tool import enrich_result, save_output_file, truncate_middle

LIST_MCP_RESOURCES_TOOL = "list_mcp_resources"
LIST_MCP_RESOURCE_TEMPLATES_TOOL = "list_mcp_resource_templates"
READ_MCP_RESOURCE_TOOL = "read_mcp_resource"

LIST_OUTPUT_MAX_BYTES = 20 * 1024

# MCP App resources are HTML user interfaces meant for a host that renders them.
# Handing the model that markup would only invite it to misread the page as
# content, so they are filtered out of every listing.
_MCP_APP_URI_PREFIX = "ui://"
_MCP_APP_MIME = re.compile(r";\s*profile\s*=\s*\"?mcp-app\"?", re.IGNORECASE)


def is_mcp_app_resource(item: dict[str, Any]) -> bool:
    uri = str(item.get("uri") or item.get("uriTemplate") or "")
    if uri.startswith(_MCP_APP_URI_PREFIX):
        return True
    return bool(_MCP_APP_MIME.search(str(item.get("mimeType") or "")))


def listed(server: str, item: dict[str, Any]) -> dict[str, Any]:
    """A listing entry tagged with its server, without host decoration.

    ``_meta`` and ``icons`` are dropped: neither is anything a model can act on,
    and between them they are most of a typical entry.
    """
    return {"server": server, **{k: v for k, v in item.items() if k not in ("_meta", "icons")}}


def error_message(exc: BaseException) -> str:
    return str(exc) or exc.__class__.__name__


@dataclass
class McpResourceServer:
    """One connected server's slice of the resources API."""

    name: str
    timeout_ms: int
    list_page: Any
    """``async (cursor, options) -> {"<key>": [...], "nextCursor": ...}``."""
    list_all: Any
    """``async (options) -> [item, ...]`` -- every page, no cursor."""
    read: Any
    """``async (uri, options) -> {"contents": [...]}``."""


def _list_params_model() -> type[BaseModel]:
    return json_schema_to_pydantic(
        LIST_MCP_RESOURCES_TOOL,
        {
            "type": "object",
            "properties": {
                "server": {
                    "type": "string",
                    "description": "MCP server name. Omit to list every server with resources.",
                },
                "cursor": {
                    "type": "string",
                    "description": (
                        "Opaque cursor from a previous call with the same server; "
                        "omit for the first page."
                    ),
                },
            },
            "additionalProperties": False,
        },
    )


def _read_params_model() -> type[BaseModel]:
    return json_schema_to_pydantic(
        READ_MCP_RESOURCE_TOOL,
        {
            "type": "object",
            "properties": {
                "server": {
                    "type": "string",
                    "description": (
                        "MCP server name exactly as configured. Must match the "
                        "'server' field returned by list_mcp_resources."
                    ),
                },
                "uri": {
                    "type": "string",
                    "description": (
                        "Resource URI to read. Must be one of the URIs returned by "
                        "list_mcp_resources."
                    ),
                },
            },
            "required": ["server", "uri"],
            "additionalProperties": False,
        },
    )


def _string_argument(params: Any, key: str) -> str | None:
    value = getattr(params, key, None) if params is not None else None
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value.strip() or None


def _json_result(payload: dict[str, Any]) -> ToolResult:
    text = json.dumps(payload, indent=2)
    rendered, truncated = truncate_middle(text, LIST_OUTPUT_MAX_BYTES)
    if truncated:
        # A full survey of every server's resources is exactly the payload that
        # can get large, and a path to the rest beats a silently cut list.
        try:
            path = save_output_file(text.encode("utf-8"), ".json")
        except OSError as exc:
            rendered = f"{rendered}\n\n[Could not save the full listing: {exc}]"
        else:
            rendered = f"{rendered}\n\n[Full listing: {path}]"
    return ToolResult(success=True, result=rendered)


class _ResourceTool(BaseTool):
    """Shared plumbing: argument reading, server lookup, and error reporting."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        params_model: type[BaseModel],
        servers: Any,
        exposure: str = "codemode",
    ) -> None:
        self.name = name
        self.description = description
        self.params = params_model
        #: ``() -> list[McpResourceServer]``, read at call time. Servers connect
        #: and disconnect during a session, so a captured list would go stale.
        self._servers = servers
        self.tool_icon = "⇄"
        self.prompt_guidelines = ()
        # Reading a resource cannot change the world, so it never prompts.
        self.mutating = False
        # These three front every server that publishes resources, so their
        # exposure is the widest of the group: a capability one connected server
        # offers should not disappear because a second server is configured more
        # narrowly.
        self.exposure = exposure
        self.namespace = "mcp"

    def _find_server(self, name: str) -> McpResourceServer:
        available = self._servers()
        for server in available:
            if server.name == name:
                return server
        names = ", ".join(s.name for s in available)
        raise ValueError(
            f'MCP server "{name}" has no resources'
            + (f". Servers with resources: {names}" if names else "")
        )

    def _available(self) -> list[McpResourceServer]:
        # Sorted so an unsorted listing is stable across calls, which matters
        # for a model comparing two results.
        return sorted(self._servers(), key=lambda s: s.name)

    def format_call(self, params: Any) -> str:
        server = getattr(params, "server", None) or "all"
        uri = getattr(params, "uri", None)
        return f"{self.name} server={server}" + (f" uri={uri}" if uri else "")

    async def execute(self, params: Any, cancel_event: asyncio.Event | None = None) -> ToolResult:
        try:
            return await self._run(params, cancel_event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A wrong server name or a bad cursor is the model's mistake to fix,
            # so the message goes back verbatim rather than becoming a bare
            # "tool failed".
            return ToolResult(success=False, result=f"{self.name} failed: {exc}")

    async def _run(self, params: Any, cancel_event: asyncio.Event | None) -> ToolResult:
        raise NotImplementedError

    async def _list(self, params: Any, cancel_event: asyncio.Event | None, key: str) -> ToolResult:
        server_name = _string_argument(params, "server")
        cursor = _string_argument(params, "cursor")
        available = self._available()

        if server_name:
            # One page of one server: the model is navigating a specific list
            # rather than surveying everything, so paging is explicit.
            server = self._find_server(server_name)
            page = await server.list_page(
                cursor, McpRequestOptions(cancel_event=cancel_event, timeout_ms=server.timeout_ms)
            )
            items = [
                listed(server.name, item)
                for item in page.get(key, [])
                if isinstance(item, dict) and not is_mcp_app_resource(item)
            ]
            payload: dict[str, Any] = {"server": server.name, key: items}
            if page.get("nextCursor") is not None:
                payload["nextCursor"] = page["nextCursor"]
            return _json_result(payload)

        if cursor:
            # Without a server this call walks every page of every server, so a
            # cursor from one server's list has no meaning. Ignoring it would
            # quietly return the wrong thing.
            raise ValueError("cursor can only be used when a server is specified")

        if not available:
            return _json_result({key: []})

        outcomes = await asyncio.gather(
            *(
                server.list_all(
                    McpRequestOptions(cancel_event=cancel_event, timeout_ms=server.timeout_ms)
                )
                for server in available
            ),
            return_exceptions=True,
        )
        items = []
        errors = []
        for server, outcome in zip(available, outcomes, strict=True):
            # One server that cannot answer must not hide the ones that can, so
            # failures are collected and reported rather than raised.
            if isinstance(outcome, BaseException):
                errors.append({"server": server.name, "error": error_message(outcome)})
                continue
            items.extend(
                listed(server.name, item)
                for item in outcome
                if isinstance(item, dict) and not is_mcp_app_resource(item)
            )
        payload = {key: items}
        if errors:
            payload["errors"] = errors
        return _json_result(payload)


class McpListResourcesTool(_ResourceTool):
    def __init__(self, servers: Any, *, exposure: str = "codemode") -> None:
        super().__init__(
            name=LIST_MCP_RESOURCES_TOOL,
            description=(
                "Lists resources provided by MCP servers. Resources let a server share "
                "context for a model, such as files, database schemas, or "
                "application-specific information. Prefer a server's resources over a "
                "web search when it offers them."
            ),
            params_model=_list_params_model(),
            servers=servers,
            exposure=exposure,
        )

    async def _run(self, params: Any, cancel_event: asyncio.Event | None) -> ToolResult:
        return await self._list(params, cancel_event, "resources")


class McpListResourceTemplatesTool(_ResourceTool):
    def __init__(self, servers: Any, *, exposure: str = "codemode") -> None:
        super().__init__(
            name=LIST_MCP_RESOURCE_TEMPLATES_TOOL,
            description=(
                "Lists resource templates provided by MCP servers. A template is a "
                "parameterized resource, such as a file pattern, and is how a server "
                "offers something it will not enumerate up front."
            ),
            params_model=_list_params_model(),
            servers=servers,
            exposure=exposure,
        )

    async def _run(self, params: Any, cancel_event: asyncio.Event | None) -> ToolResult:
        return await self._list(params, cancel_event, "resourceTemplates")


class McpReadResourceTool(_ResourceTool):
    def __init__(self, servers: Any, *, exposure: str = "codemode") -> None:
        super().__init__(
            name=READ_MCP_RESOURCE_TOOL,
            description=(
                "Read a specific resource from an MCP server given the server name "
                "and resource URI."
            ),
            params_model=_read_params_model(),
            servers=servers,
            exposure=exposure,
        )

    async def _run(self, params: Any, cancel_event: asyncio.Event | None) -> ToolResult:
        server_name = _string_argument(params, "server")
        uri = _string_argument(params, "uri")
        if not server_name:
            raise ValueError("server must be provided")
        if not uri:
            raise ValueError("uri must be provided")

        server = self._find_server(server_name)
        result = await server.read(
            uri, McpRequestOptions(cancel_event=cancel_event, timeout_ms=server.timeout_ms)
        )
        contents = result.get("contents")
        if not isinstance(contents, list):
            contents = []

        blocks: list[dict[str, Any]] = []
        for entry in contents:
            if not isinstance(entry, dict):
                continue
            # More than one content means a directory-like resource, where bare
            # text would be unattributable without its URI as a label.
            if len(contents) > 1:
                blocks.append({"type": "text", "text": f"{entry.get('uri', uri)}:"})
            blocks.append({"type": "resource", "resource": entry})

        if not blocks:
            return ToolResult(success=True, result=f"Resource {uri} is empty.")

        # enrich_result turns a text resource into text and a binary one into a
        # file path, then split_content flattens the blocks into the one-text-
        # plus-images shape ToolResult takes. Same path a tool result takes, so
        # a resource reads the same as a tool that returned the same bytes.
        text, images = split_content(to_tool_content(enrich_result({"content": blocks})))
        rendered, truncated = truncate_middle(text, LIST_OUTPUT_MAX_BYTES)
        if truncated:
            try:
                path = save_output_file(text.encode("utf-8", "replace"), ".txt")
            except OSError as exc:
                rendered = f"{rendered}\n\n[Could not save the full resource: {exc}]"
            else:
                rendered = f"{rendered}\n\n[Full resource: {path}]"
        return ToolResult(success=True, result=rendered or None, images=images or None)


def connected_resource_servers(manager: Any, kind: str = "resources") -> list[McpResourceServer]:
    """The manager's connected servers, adapted for the resources API.

    Only connected servers are included. One that is not connected cannot
    answer, and offering it would send the model to a dead end -- or worse, let
    it conclude the resource does not exist.

    ``kind`` is ``"resources"`` or ``"templates"``: the two list tools ask for
    different things, and no single client call answers both.
    """
    templates = kind == "templates"
    servers: list[McpResourceServer] = []
    for connection in getattr(manager, "servers", {}).values():
        client = getattr(connection, "client", None)
        if client is None or not getattr(client, "connected", False):
            continue
        # `client.connected` goes true as soon as the transport is up, which is
        # before the MCP handshake finishes. A server still inside its startup
        # window cannot answer a resource call, so require the same readiness
        # the manager's own tool listing uses.
        if getattr(getattr(connection, "status", None), "state", None) != "connected":
            continue
        timeout_ms = int(getattr(connection.config, "timeout_seconds", 60) or 60) * 1000
        servers.append(
            McpResourceServer(
                name=connection.config.name,
                timeout_ms=timeout_ms,
                list_page=(
                    client.list_resource_templates_page
                    if templates
                    else client.list_resources_page
                ),
                list_all=(client.list_resource_templates if templates else client.list_resources),
                read=client.read_resource,
            )
        )
    return servers


def create_mcp_resource_tools(manager: Any) -> list[BaseTool]:
    """The three resource tools, over the manager's connected servers.

    Empty when nothing is connected: three tools that can only ever return an
    empty list are noise in every session that has no MCP server at all, and the
    model is not missing anything by not seeing them.
    """
    # Gate on a server that is actually connected, not merely configured: a
    # server that failed to start still has a manager entry, and offering three
    # tools that can only ever return an empty list is the noise this avoids.
    if not connected_resource_servers(manager, "resources") and not connected_resource_servers(
        manager, "templates"
    ):
        return []
    # Widest exposure among the servers they front. A `direct` server's
    # resources are then declared as ordinary tool calls and a `codemode` one is
    # reachable from a script; taking the narrowest would hide a capability a
    # connected server genuinely publishes.
    exposure = _widest_exposure(manager)
    return [
        McpListResourcesTool(
            lambda: connected_resource_servers(manager, "resources"), exposure=exposure
        ),
        McpListResourceTemplatesTool(
            lambda: connected_resource_servers(manager, "templates"), exposure=exposure
        ),
        McpReadResourceTool(
            lambda: connected_resource_servers(manager, "resources"), exposure=exposure
        ),
    ]


def _widest_exposure(manager: Any) -> str:
    """The most permissive exposure across every connected server."""
    from vtx.mcp.exposure import widest

    values: list[str] = []
    for connection in getattr(manager, "servers", {}).values():
        config = getattr(connection, "config", None)
        if (
            config is None
            or getattr(getattr(connection, "status", None), "state", "") != "connected"
        ):
            continue
        exposure = getattr(config, "exposure", None)
        values.append(exposure if isinstance(exposure, str) else "codemode")
    return widest(*values) if values else "codemode"


__all__ = [
    "LIST_MCP_RESOURCES_TOOL",
    "LIST_MCP_RESOURCE_TEMPLATES_TOOL",
    "READ_MCP_RESOURCE_TOOL",
    "McpListResourceTemplatesTool",
    "McpListResourcesTool",
    "McpReadResourceTool",
    "McpResourceServer",
    "connected_resource_servers",
    "create_mcp_resource_tools",
    "is_mcp_app_resource",
    "listed",
]
