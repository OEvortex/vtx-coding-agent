"""Model Context Protocol client for vtx.

Transport-neutral core plus stdio and Streamable HTTP transports. A transport
owns framing and I/O; :class:`McpClient` owns request correlation,
initialization, timeouts, cancellation, and the protocol helpers.

This is a top-level package, sibling to ``vtx.coding_agent`` and ``vtx.tui``,
because the tool adapter (:mod:`vtx.mcp.tool`) builds on the harness tool
contract in ``vtx.ai.agent.tools``. The harness itself never imports this
package; the product layer wires it up. So the direction is
``core <- ai <- mcp``, with ``coding_agent`` and ``tui`` on top.

Typical use goes through :mod:`vtx.mcp.manager`, which reads server config
and registers each server's tools into the harness tool registry. Use the
client directly when you want one connection and no tool wiring::

    from vtx.mcp import McpClient, McpClientOptions, StdioTransport, StdioTransportOptions

    client = McpClient(McpClientOptions(name="vtx", version="1.0.0"))
    await client.connect(StdioTransport(StdioTransportOptions(command="npx", args=[...])))
    tools = await client.list_tools()
    await client.close()
"""

from .auth import AuthProvider, UnauthorizedContext
from .client import (
    DEFAULT_REQUEST_TIMEOUT_MS,
    MAX_LIST_PAGES,
    McpClient,
    McpClientOptions,
    McpRequestOptions,
)
from .config import (
    LoadedMcpConfig,
    McpOAuthSettings,
    McpServerConfig,
    load_mcp_config,
    validate_mcp_server_config,
)
from .content import LlmContent, split_content, to_tool_content
from .jsonrpc import (
    JSON_RPC_INTERNAL_ERROR,
    JSON_RPC_INVALID_PARAMS,
    JSON_RPC_INVALID_REQUEST,
    JSON_RPC_METHOD_NOT_FOUND,
    JSON_RPC_PARSE_ERROR,
    JsonRpcId,
    JsonRpcMessage,
    McpAbortError,
    McpConnectionClosedError,
    McpError,
    McpTimeoutError,
    parse_jsonrpc_message,
)
from .manager import McpManager, McpServerConnection, McpServerStatus
from .tool import MCP_OUTPUT_MAX_BYTES, McpTool, create_mcp_tool_name
from .transport import DEFAULT_MAX_MESSAGE_BYTES, McpTransport, TransportEvents
from .transports.in_memory import InMemoryTransport, create_in_memory_transport_pair
from .transports.stdio import StdioTransport, StdioTransportOptions
from .transports.streamable_http import (
    McpAuthRequiredError,
    McpHttpError,
    McpSessionExpiredError,
    ReconnectOptions,
    SseEvent,
    StreamableHttpTransport,
    StreamableHttpTransportOptions,
)
from .types import (
    LATEST_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    CallToolResult,
    ContentBlock,
    Implementation,
    InitializeResult,
    ReadResourceResult,
    Resource,
    ResourceTemplate,
    Root,
    Tool,
)

__all__ = [
    "DEFAULT_MAX_MESSAGE_BYTES",
    "DEFAULT_REQUEST_TIMEOUT_MS",
    "JSON_RPC_INTERNAL_ERROR",
    "JSON_RPC_INVALID_PARAMS",
    "JSON_RPC_INVALID_REQUEST",
    "JSON_RPC_METHOD_NOT_FOUND",
    "JSON_RPC_PARSE_ERROR",
    "LATEST_PROTOCOL_VERSION",
    "MAX_LIST_PAGES",
    "MCP_OUTPUT_MAX_BYTES",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "AuthProvider",
    "CallToolResult",
    "ContentBlock",
    "Implementation",
    "InMemoryTransport",
    "InitializeResult",
    "JsonRpcId",
    "JsonRpcMessage",
    "LlmContent",
    "LoadedMcpConfig",
    "McpAbortError",
    "McpAuthRequiredError",
    "McpClient",
    "McpClientOptions",
    "McpConnectionClosedError",
    "McpError",
    "McpHttpError",
    "McpManager",
    "McpOAuthSettings",
    "McpRequestOptions",
    "McpServerConfig",
    "McpServerConnection",
    "McpServerStatus",
    "McpSessionExpiredError",
    "McpTimeoutError",
    "McpTool",
    "McpTransport",
    "ReadResourceResult",
    "ReconnectOptions",
    "Resource",
    "ResourceTemplate",
    "Root",
    "SseEvent",
    "StdioTransport",
    "StdioTransportOptions",
    "StreamableHttpTransport",
    "StreamableHttpTransportOptions",
    "Tool",
    "TransportEvents",
    "UnauthorizedContext",
    "create_in_memory_transport_pair",
    "create_mcp_tool_name",
    "load_mcp_config",
    "parse_jsonrpc_message",
    "split_content",
    "to_tool_content",
    "validate_mcp_server_config",
]
