"""MCP protocol constants and the validators for server result shapes.

The shapes are documented here as ``TypedDict``s for reading, but everything
crosses the wire as ``dict[str, Any]`` (see :mod:`vtx.mcp.jsonrpc` for why).
Each ``validate_*`` function returns the same dict it was given once the shape
checks out, so unknown fields survive.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict, cast

from .jsonrpc import McpError, invalid, is_object

LATEST_PROTOCOL_VERSION = "2025-11-25"
"""Version we request in ``initialize``."""

SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (
    LATEST_PROTOCOL_VERSION,
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
"""Versions we accept. A server built on an older SDK answers with its own
latest, so older versions stay accepted rather than failing the connect."""


class Implementation(TypedDict):
    name: str
    version: str
    title: NotRequired[str]


class Root(TypedDict):
    uri: str
    name: NotRequired[str]


class ClientCapabilities(TypedDict, total=False):
    experimental: dict[str, Any]
    roots: dict[str, Any]
    sampling: dict[str, Any]
    elicitation: dict[str, Any]


class ServerCapabilities(TypedDict, total=False):
    experimental: dict[str, Any]
    logging: dict[str, Any]
    prompts: dict[str, Any]
    resources: dict[str, Any]
    tools: dict[str, Any]
    completions: dict[str, Any]


class InitializeResult(TypedDict, total=False):
    protocolVersion: str
    capabilities: ServerCapabilities
    serverInfo: Implementation
    instructions: str


class Tool(TypedDict, total=False):
    name: str
    title: str
    description: str
    inputSchema: dict[str, Any]
    outputSchema: dict[str, Any]
    annotations: dict[str, Any]
    execution: dict[str, Any]
    _meta: dict[str, Any]


class Resource(TypedDict, total=False):
    uri: str
    name: str
    title: str
    description: str
    mimeType: str
    size: int
    annotations: dict[str, Any]
    _meta: dict[str, Any]


class ResourceTemplate(TypedDict, total=False):
    uriTemplate: str
    name: str
    title: str
    description: str
    mimeType: str
    annotations: dict[str, Any]
    _meta: dict[str, Any]


ContentBlock = dict[str, Any]
"""A ``TextContent`` / ``ImageContent`` / ``AudioContent`` /
``ResourceLinkContent`` / ``EmbeddedResourceContent`` block. Left open because
``to_tool_content`` only reads the fields it needs and passes the rest along."""

CallToolResult = dict[str, Any]
ReadResourceResult = dict[str, Any]
ListToolsResult = dict[str, Any]
ListResourcesResult = dict[str, Any]
ListResourceTemplatesResult = dict[str, Any]


def validate_initialize_result(value: Any) -> InitializeResult:
    if (
        not is_object(value)
        or not isinstance(value.get("protocolVersion"), str)
        or not is_object(value.get("capabilities"))
        or not is_object(value.get("serverInfo"))
        or not isinstance(value["serverInfo"].get("name"), str)
        or not isinstance(value["serverInfo"].get("version"), str)
        or ("instructions" in value and not isinstance(value["instructions"], str))
    ):
        raise McpError(-32600, "Invalid MCP initialize result")
    # Every field the TypedDict declares is checked above; the cast only
    # tells the checker that, which narrowing cannot express.
    return cast(InitializeResult, value)


def _is_tool(tool: dict[str, Any]) -> bool:
    return isinstance(tool.get("name"), str) and is_object(tool.get("inputSchema"))


def _is_resource(item: dict[str, Any]) -> bool:
    # ``name`` is required by the spec, but some servers omit it and the URI
    # stands in (see ``_with_resource_name``).
    return isinstance(item.get("uri"), str) and (
        item.get("name") is None or isinstance(item["name"], str)
    )


def _is_resource_template(item: dict[str, Any]) -> bool:
    return isinstance(item.get("uriTemplate"), str) and (
        item.get("name") is None or isinstance(item["name"], str)
    )


def _with_resource_name(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "name": item.get("name") or item["uri"]}


def _with_template_name(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "name": item.get("name") or item["uriTemplate"]}


def validate_list_page(
    method: str, key: str, value: Any, is_item: Any
) -> tuple[list[dict[str, Any]], str | None]:
    """Check one page of a paginated list: items under ``key``, plus the cursor."""
    items = value.get(key) if is_object(value) else None
    if not is_object(value) or not isinstance(items, list):
        raise invalid(f"Invalid MCP {method} result")
    for item in items:
        if not is_object(item) or not is_item(item):
            raise invalid(f"Invalid entry in MCP {method} result")
    cursor = value.get("nextCursor")
    if cursor is not None and not isinstance(cursor, str):
        raise invalid(f"Invalid MCP {method} cursor")
    return items, cursor


def validate_read_resource_result(value: Any) -> ReadResourceResult:
    if not is_object(value) or not isinstance(value.get("contents"), list):
        raise invalid("Invalid MCP resources/read result")
    for contents in value["contents"]:
        if (
            not is_object(contents)
            or not isinstance(contents.get("uri"), str)
            or (
                not isinstance(contents.get("text"), str)
                and not isinstance(contents.get("blob"), str)
            )
        ):
            raise invalid("Invalid contents in MCP resources/read result")
    return value


def validate_call_tool_result(value: Any) -> CallToolResult:
    """``content`` is required by the spec, but servers that only return
    ``structuredContent`` omit it (the official SDK defaults it too), so a
    missing list becomes an empty one rather than an error."""
    if not is_object(value) or (
        value.get("content") is not None and not isinstance(value["content"], list)
    ):
        raise McpError(-32600, "Invalid MCP tools/call result")
    if value.get("structuredContent") is not None and not is_object(value["structuredContent"]):
        raise McpError(-32600, "Invalid MCP tools/call structured content")
    return cast(CallToolResult, {**value, "content": value.get("content") or []})


ToolListItem = _is_tool
ResourceListItem = _is_resource
ResourceTemplateListItem = _is_resource_template

normalize_resource = _with_resource_name
normalize_resource_template = _with_template_name

ServerState = Literal["idle", "connecting", "connected", "closed"]

__all__ = [
    "LATEST_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "CallToolResult",
    "ClientCapabilities",
    "ContentBlock",
    "Implementation",
    "InitializeResult",
    "ListResourceTemplatesResult",
    "ListResourcesResult",
    "ListToolsResult",
    "ReadResourceResult",
    "Resource",
    "ResourceListItem",
    "ResourceTemplate",
    "ResourceTemplateListItem",
    "Root",
    "ServerCapabilities",
    "Tool",
    "ToolListItem",
    "normalize_resource",
    "normalize_resource_template",
    "validate_call_tool_result",
    "validate_initialize_result",
    "validate_list_page",
    "validate_read_resource_result",
]
