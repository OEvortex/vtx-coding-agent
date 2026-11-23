"""JSON-RPC 2.0 framing, message validation, and error types for MCP.

Wire values stay as ``dict[str, Any]`` rather than dataclasses. MCP servers
add fields over time (``_meta``, ``annotations``, ``execution``) and a typed
model silently drops what it does not know about; a dict round-trips whatever
arrives. The validators below are the single place where an incoming shape is
trusted, mirroring the reference implementation's boundary checks.
"""

from __future__ import annotations

from typing import Any, TypeGuard

JsonRpcId = str | int
JsonRpcMessage = dict[str, Any]

JSON_RPC_PARSE_ERROR = -32700
JSON_RPC_INVALID_REQUEST = -32600
JSON_RPC_METHOD_NOT_FOUND = -32601
JSON_RPC_INVALID_PARAMS = -32602
JSON_RPC_INTERNAL_ERROR = -32603


class McpError(Exception):
    """A JSON-RPC error returned by a server, or raised while validating one."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data

    def __str__(self) -> str:
        return f"[{self.code}] {super().__str__()}"


class McpConnectionClosedError(Exception):
    """The transport is closed, or the client is not connected."""

    def __init__(self, message: str = "MCP connection closed") -> None:
        super().__init__(message)


class McpTimeoutError(Exception):
    def __init__(self, timeout_ms: int) -> None:
        super().__init__(f"MCP request timed out after {timeout_ms}ms")
        self.timeout_ms = timeout_ms


class McpAbortError(Exception):
    """The caller cancelled the request (an ``asyncio.Event`` was set)."""


def is_object(value: Any) -> TypeGuard[dict[str, Any]]:
    """True for a JSON object.

    A ``TypeGuard`` rather than a plain ``bool`` so that ``is_object(x)``
    narrows ``x`` for a type checker as well as at runtime, which is the whole
    point of the boundary checks that use it.
    """
    return isinstance(value, dict)


def is_jsonrpc_id(value: Any) -> bool:
    return isinstance(value, str) or (
        isinstance(value, (int, float)) and not isinstance(value, bool)
    )


def is_jsonrpc_request(message: Any) -> bool:
    return (
        is_object(message)
        and message.get("jsonrpc") == "2.0"
        and is_jsonrpc_id(message.get("id"))
        and isinstance(message.get("method"), str)
    )


def is_jsonrpc_notification(message: Any) -> bool:
    return (
        is_object(message)
        and message.get("jsonrpc") == "2.0"
        and "id" not in message
        and isinstance(message.get("method"), str)
    )


def is_jsonrpc_response(message: Any) -> bool:
    if (
        not is_object(message)
        or message.get("jsonrpc") != "2.0"
        or not is_jsonrpc_id(message.get("id"))
    ):
        return False
    if "result" in message:
        return "error" not in message
    error = message.get("error")
    if not is_object(error):
        return False
    return isinstance(error.get("code"), (int, float)) and isinstance(error.get("message"), str)


def parse_jsonrpc_message(value: Any) -> JsonRpcMessage:
    if is_jsonrpc_request(value) or is_jsonrpc_notification(value) or is_jsonrpc_response(value):
        return value
    raise McpError(JSON_RPC_INVALID_REQUEST, "Invalid JSON-RPC message")


def invalid(message: str) -> McpError:
    return McpError(JSON_RPC_INVALID_REQUEST, message)


__all__ = [
    "JSON_RPC_INTERNAL_ERROR",
    "JSON_RPC_INVALID_PARAMS",
    "JSON_RPC_INVALID_REQUEST",
    "JSON_RPC_METHOD_NOT_FOUND",
    "JSON_RPC_PARSE_ERROR",
    "JsonRpcId",
    "JsonRpcMessage",
    "McpAbortError",
    "McpConnectionClosedError",
    "McpError",
    "McpTimeoutError",
    "invalid",
    "is_jsonrpc_id",
    "is_jsonrpc_notification",
    "is_jsonrpc_request",
    "is_jsonrpc_response",
    "is_object",
    "parse_jsonrpc_message",
]
