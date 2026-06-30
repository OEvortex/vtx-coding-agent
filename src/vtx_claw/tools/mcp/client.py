"""MCP Client for vtx_claw — connects to MCP servers, calls tools, manages lifecycle.

Uses the official MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk

This client is entirely internal to vtx_claw and is NOT exposed to vtx's tool
registry or LLM provider. It is only used by the MCPProxyTool.

Adapted from Jarvis's MCPClient.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation

from vtx_claw.tools.mcp.capabilities import (
    MCPPromptMessage,
    MCPPromptSpec,
    MCPResourceContent,
    MCPResourceSpec,
    MCPServerCapabilities,
)
from vtx_claw.tools.mcp.config import MCPServerConfig, MCPToolSpec, MCPTransportType

logger = logging.getLogger(__name__)


class MCPClient:
    """Client for connecting to MCP servers using the official MCP SDK.

    Supports tools, resources, and prompts with automatic capability negotiation.
    Each instance manages a single server connection.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._session: ClientSession | None = None
        self._tools: list[MCPToolSpec] = []
        self._resources: list[MCPResourceSpec] = []
        self._prompts: list[MCPPromptSpec] = []
        self._capabilities: MCPServerCapabilities = MCPServerCapabilities()
        self._initialized = False
        self._lock = asyncio.Lock()
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._run_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready_event: asyncio.Event | None = None
        self._connect_error: Exception | None = None
        self._connect_time: float = 0.0
        self._last_error: str | None = None
        self._last_tool_call: float = 0.0

    async def _reset_client(self) -> None:
        """Reset the client state."""
        if self._stop_event:
            self._stop_event.set()
        if self._run_task and not self._run_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._run_task), timeout=5.0)
            except Exception as e:
                logger.warning(f"Error waiting for MCP client task shutdown: {e}")
                self._run_task.cancel()
        self._run_task = None
        self._stop_event = None
        self._ready_event = None
        self._session = None
        self._tools = []
        self._resources = []
        self._prompts = []
        self._capabilities = MCPServerCapabilities()
        self._initialized = False
        self._event_loop = None
        self._connect_error = None
        self._last_error = None

    async def _ensure_active_loop(self) -> None:
        """Reset state if this client was initialized on another event loop."""
        current_loop = asyncio.get_running_loop()
        if self._event_loop is None:
            self._event_loop = current_loop
            return
        stored_loop_closed = self._event_loop.is_closed()
        if self._event_loop is not current_loop or stored_loop_closed:
            logger.info(
                "MCP client event loop changed or closed; resetting client for server '%s'",
                self.config.name,
            )
            await self._reset_client()
            self._event_loop = current_loop

    async def connect(self) -> None:
        """Connect to the MCP server."""
        await self._ensure_active_loop()

        if self._initialized:
            return

        try:
            self._stop_event = asyncio.Event()
            self._ready_event = asyncio.Event()
            self._connect_error = None

            self._run_task = asyncio.create_task(self._run_client_task())

            await self._ready_event.wait()

            if self._connect_error:
                raise self._connect_error

            self._negotiate_capabilities()

            await self._list_tools()

            if self.config.auto_discover_capabilities:
                if self._capabilities.resources:
                    await self._list_resources()
                if self._capabilities.prompts:
                    await self._list_prompts()

            self._initialized = True
            caps_desc = ", ".join(k for k, v in self._capabilities.to_dict().items() if v)
            logger.info(
                "Connected to MCP server '%s' with %d tools [capabilities: %s]",
                self.config.name,
                len(self._tools),
                caps_desc or "none",
            )

        except Exception as e:
            logger.error("Failed to connect to MCP server '%s': %s", self.config.name, e)
            await self._reset_client()
            raise

    async def _run_client_task(self) -> None:
        """Background task that keeps the MCP context managers open."""
        try:
            from contextlib import AsyncExitStack

            async with AsyncExitStack() as stack:
                if self.config.transport == MCPTransportType.STDIO:
                    import os

                    full_env = os.environ.copy()
                    full_env.update(self.config.env)

                    server_params = StdioServerParameters(
                        command=self.config.command, args=self.config.args, env=full_env
                    )

                    logger.info(
                        "Connecting to MCP server via stdio: %s %s",
                        self.config.command,
                        self.config.args,
                    )
                    ctx = stdio_client(server_params)
                    streams = await stack.enter_async_context(ctx)
                    read_stream, write_stream = streams

                elif self.config.transport in (MCPTransportType.HTTP, MCPTransportType.SSE):
                    if not self.config.url:
                        raise ValueError(
                            f"No URL configured for HTTP MCP server '{self.config.name}'"
                        )
                    logger.info("Connecting to MCP server via HTTP: %s", self.config.url)
                    http_client = await self._build_http_client(stack)
                    if http_client:
                        ctx = streamable_http_client(self.config.url, http_client=http_client)
                    else:
                        ctx = streamable_http_client(self.config.url)
                    streams = await stack.enter_async_context(ctx)
                    if len(streams) == 3:
                        read_stream, write_stream, _ = streams
                    else:
                        read_stream, write_stream = streams
                else:
                    raise ValueError(f"Unknown transport: {self.config.transport}")

                session_kwargs: dict[str, Any] = {
                    "client_info": Implementation(name="vtx-claw", version="0.1.0")
                }

                session_ctx = ClientSession(read_stream, write_stream, **session_kwargs)
                self._session = await stack.enter_async_context(session_ctx)

                await self._session.initialize()

                logger.info("MCP transport initialized for %s", self.config.name)

                import time

                self._connect_time = time.time()

                if self._ready_event:
                    self._ready_event.set()

                if self._stop_event:
                    await self._stop_event.wait()

        except Exception as e:
            self._connect_error = e
            self._last_error = str(e)
            if self._ready_event and not self._ready_event.is_set():
                self._ready_event.set()
            logger.debug("MCP client task for %s ended with exception: %s", self.config.name, e)

    async def _list_tools(self) -> None:
        """List available tools from the MCP server."""
        if not self._session:
            raise RuntimeError("MCP session not initialized")
        response = await self._session.list_tools()
        tools = response.tools if hasattr(response, "tools") else []
        self._tools = [
            MCPToolSpec(
                name=tool.name,
                description=tool.description or "",
                input_schema=(tool.inputSchema if hasattr(tool, "inputSchema") else {}),
                server_name=self.config.name,
                remote_name=tool.name,
            )
            for tool in tools
        ]

    def _negotiate_capabilities(self) -> None:
        """Negotiate server capabilities after initialize()."""
        if not self._session:
            return
        server_caps = self._session.get_server_capabilities()
        self._capabilities = MCPServerCapabilities.from_server_capabilities(server_caps)
        logger.debug(
            "MCP server '%s' capabilities: %s", self.config.name, self._capabilities.to_dict()
        )

    def get_capabilities(self) -> MCPServerCapabilities:
        """Get the negotiated server capabilities."""
        return self._capabilities

    # -- Resources -----------------------------------------------------------

    async def _list_resources(self) -> None:
        """List available resources from the MCP server."""
        if not self._session or not self._capabilities.resources:
            return
        try:
            response = await self._session.list_resources()
            resources = response.resources if hasattr(response, "resources") else []
            self._resources = [MCPResourceSpec.from_sdk(r, self.config.name) for r in resources]
            logger.debug(
                "Discovered %d resources from '%s'", len(self._resources), self.config.name
            )
        except Exception as e:
            logger.warning("Failed to list resources from '%s': %s", self.config.name, e)

    async def list_resources(self) -> list[MCPResourceSpec]:
        """List available resources from the MCP server."""
        await self._ensure_active_loop()
        if not self._initialized:
            await self.connect()
        return self._resources

    async def read_resource(self, uri: str) -> list[MCPResourceContent]:
        """Read a resource from the MCP server."""
        await self._ensure_active_loop()
        if not self._initialized:
            await self.connect()
        if not self._session:
            raise RuntimeError("MCP session not initialized")
        try:
            from pydantic import AnyUrl

            response = await self._session.read_resource(AnyUrl(uri))
            contents: list[MCPResourceContent] = []

            if hasattr(response, "contents"):
                for item in response.contents:
                    content = MCPResourceContent(uri=str(item.uri))
                    mime = getattr(item, "mimeType", None)
                    if mime:
                        content.mime_type = str(mime)
                    text = getattr(item, "text", None)
                    if text:
                        content.text = str(text)
                    elif hasattr(item, "blob") and item.blob:
                        import base64

                        blob_val = item.blob
                        content.blob = base64.b64decode(str(blob_val))
                    contents.append(content)
            return contents

        except Exception as e:
            logger.error("Failed to read resource '%s' from '%s': %s", uri, self.config.name, e)
            raise

    # -- Prompts -------------------------------------------------------------

    async def _list_prompts(self) -> None:
        """List available prompts from the MCP server."""
        if not self._session or not self._capabilities.prompts:
            return
        try:
            response = await self._session.list_prompts()
            prompts = response.prompts if hasattr(response, "prompts") else []
            self._prompts = [MCPPromptSpec.from_sdk(p, self.config.name) for p in prompts]
            logger.debug("Discovered %d prompts from '%s'", len(self._prompts), self.config.name)
        except Exception as e:
            logger.warning("Failed to list prompts from '%s': %s", self.config.name, e)

    async def list_prompts(self) -> list[MCPPromptSpec]:
        """List available prompts from the MCP server."""
        await self._ensure_active_loop()
        if not self._initialized:
            await self.connect()
        return self._prompts

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> list[MCPPromptMessage]:
        """Get a rendered prompt from the MCP server."""
        await self._ensure_active_loop()
        if not self._initialized:
            await self.connect()
        if not self._session:
            raise RuntimeError("MCP session not initialized")
        try:
            response = await self._session.get_prompt(name, arguments=arguments or {})
            messages: list[MCPPromptMessage] = []
            if hasattr(response, "messages"):
                for msg in response.messages:
                    role = str(msg.role) if msg.role else "user"
                    content = ""
                    if hasattr(msg, "content"):
                        if isinstance(msg.content, str):
                            content = msg.content
                        elif hasattr(msg.content, "text"):
                            content = str(msg.content.text)
                        else:
                            content = str(msg.content)
                    messages.append(MCPPromptMessage(role=role, content=content))
            return messages
        except Exception as e:
            logger.error("Failed to get prompt '%s' from '%s': %s", name, self.config.name, e)
            raise

    # -- Tool calling --------------------------------------------------------

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool on the MCP server."""
        await self._ensure_active_loop()
        if not self._initialized:
            await self.connect()
        if not self._session:
            raise RuntimeError("MCP session not initialized")
        try:
            async with self._lock:
                result = await self._session.call_tool(tool_name, arguments)
                import time

                self._last_tool_call = time.time()

                content: list[dict[str, Any]] = []
                is_error = False

                if hasattr(result, "content"):
                    for item in result.content:
                        if hasattr(item, "text"):
                            content.append({"type": "text", "text": item.text})
                        elif hasattr(item, "data"):
                            mime_type = getattr(item, "mimeType", "application/octet-stream")
                            content.append(
                                {"type": "image", "data": item.data, "mimeType": mime_type}
                            )

                if hasattr(result, "isError"):
                    is_error = result.isError

                return {"content": content, "isError": is_error}

        except Exception as e:
            logger.error("MCP tool call failed: %s", e)
            raise

    async def list_tools(self) -> list[MCPToolSpec]:
        """List available tools from the MCP server."""
        await self._ensure_active_loop()
        if not self._initialized:
            await self.connect()
        return self._tools

    # -- HTTP client with auth -----------------------------------------------

    async def _build_http_client(self, stack: Any) -> Any:
        """Build an authenticated httpx.AsyncClient if auth is configured."""
        import httpx

        auth_config = self.config.auth
        if not auth_config or not auth_config.is_configured:
            return None

        if auth_config.type == "bearer":
            token = auth_config.get_token()
            if not token:
                logger.warning("No bearer token configured for '%s'", self.config.name)
                return None
            return httpx.AsyncClient(
                headers={auth_config.header_name: f"{auth_config.header_prefix} {token}"}
            )

        if auth_config.type == "api_key":
            token = auth_config.get_token()
            if not token:
                logger.warning("No API key configured for '%s'", self.config.name)
                return None
            return httpx.AsyncClient(headers={auth_config.header_name: token})

        return None

    # -- Lifecycle -----------------------------------------------------------

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        await self._reset_client()
        self._event_loop = None

    @property
    def is_connected(self) -> bool:
        """Check if connected to MCP server."""
        return self._initialized

    @property
    def server_name(self) -> str:
        """Get the server name."""
        return self.config.name

    @property
    def tool_count(self) -> int:
        """Get the number of available tools."""
        return len(self._tools)

    @property
    def resource_count(self) -> int:
        """Get the number of available resources."""
        return len(self._resources)

    @property
    def prompt_count(self) -> int:
        """Get the number of available prompts."""
        return len(self._prompts)

    @property
    def last_error(self) -> str | None:
        """Get the last connection error."""
        return self._last_error

    @property
    def transport_type(self) -> str:
        """Get the transport type."""
        return self.config.transport
