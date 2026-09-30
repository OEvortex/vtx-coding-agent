"""Owns the configured MCP servers and the tools they contribute.

One :class:`McpServerConnection` per server, each holding a client that is
connected lazily and reconnected when it drops. :class:`McpManager` is the
lifecycle owner: connect at startup (bounded, so one slow server does not hold
up a session), expose the live tools, and close everything on shutdown.

Closing matters more than it looks. A stdio server is a child process holding a
pipe; leaking one orphans the process and leaves the pipe open.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from vtx.ai.agent.tools.base import BaseTool
from vtx.core.paths import get_config_dir
from vtx.core.version import VERSION

from .client import McpClient, McpClientOptions, McpRequestOptions
from .config import LoadedMcpConfig, McpServerConfig, load_mcp_config
from .jsonrpc import McpConnectionClosedError
from .tasks import spawn
from .tool import McpTool, McpToolCaller, create_mcp_tool_name
from .transports.stdio import StdioTransport, StdioTransportOptions
from .transports.streamable_http import (
    McpAuthRequiredError,
    StreamableHttpTransport,
    StreamableHttpTransportOptions,
)
from .types import Root
from .types import Tool as McpToolDef

log = logging.getLogger("mcp.manager")

DEFAULT_STARTUP_WAIT_SECONDS = 10.0
CLIENT_NAME = "vtx"


@dataclass
class McpServerStatus:
    """What ``/mcp`` shows for one server. Never raises; it is a report."""

    name: str
    state: str = "disabled"
    error: str | None = None
    tool_count: int = 0
    instructions: str | None = None
    source: str = ""
    scope: str = "global"

    def describe(self) -> str:
        if self.state == "connected":
            plural = "s" if self.tool_count != 1 else ""
            return f"connected ({self.tool_count} tool{plural})"
        if self.state == "needs-auth":
            return "needs sign-in"
        if self.error:
            return f"{self.state}: {self.error}"
        return self.state


def cwd_to_uri(cwd: str) -> str:
    return "file://" + quote(str(Path(cwd).resolve()))


class McpServerConnection:
    """One configured server: its transport, client, and tool definitions.

    The client connects lazily and is re-created when it drops, so a server
    that exits (or is restarted) does not require the user to reload the
    session. ``on_change`` is called when the server's tool list changed, so
    the manager can rebuild the tool set.
    """

    def __init__(
        self, config: McpServerConfig, *, roots: list[Root] | None = None, on_change: Any = None
    ) -> None:
        self.config = config
        self.status = McpServerStatus(
            name=config.name,
            state="disabled" if not config.enabled else "connecting",
            source=config.source,
            scope=config.scope,
        )
        self.client: McpClient | None = None
        self.tools: list[McpTool] = []
        self.definitions: list[McpToolDef] = []
        self._roots = list(roots or [])
        self._on_change = on_change
        self._opening: asyncio.Task | None = None
        self._closed = False
        self._dispose_tools_changed: Any = None

    # ---- transport --------------------------------------------------------

    def _create_transport(self):
        if self.config.is_stdio:
            return StdioTransport(
                StdioTransportOptions(
                    command=self.config.command or "",
                    args=list(self.config.args),
                    cwd=self.config.cwd,
                    env=self.config.resolved_env(),
                )
            )
        return StreamableHttpTransport(
            StreamableHttpTransportOptions(
                url=self.config.url or "", headers=self.config.resolved_headers()
            )
        )

    def _client_options(self) -> McpClientOptions:
        return McpClientOptions(
            name=CLIENT_NAME,
            version=VERSION,
            # Exposing the working directory lets a filesystem-style server
            # resolve relative paths the way the user typed them.
            roots=self._roots or None,
        )

    # ---- connection -------------------------------------------------------

    async def get_client(self) -> McpClient:
        """A connected client, connecting or reconnecting as needed."""
        if self._closed:
            raise McpConnectionClosedError(f'MCP server "{self.config.name}" is shut down')
        if self.client is not None and self.client.connected:
            return self.client
        if self._opening is None:
            self._opening = asyncio.ensure_future(self._open())
        try:
            return await self._opening
        finally:
            self._opening = None

    async def _open(self) -> McpClient:
        self.status.state = "connecting"
        self.status.error = None
        transport = self._create_transport()
        client = McpClient(self._client_options())
        # Errors on a live connection are logged, not raised: a stray server
        # log line must not take down every subsequent tool call.
        client.on_error(lambda exc: log.debug("MCP %s: %s", self.config.name, exc))
        self._dispose_tools_changed = client.on_notification(
            "notifications/tools/list_changed", self._handle_tools_changed
        )
        try:
            await client.connect(transport)
        except McpAuthRequiredError as exc:
            # A state the user can act on, not a failure to report.
            self.status.state = "needs-auth"
            self.status.error = None
            await self._safe_close(client)
            raise McpConnectionClosedError(
                f'MCP server "{self.config.name}" requires sign-in'
            ) from exc
        except BaseException as exc:
            self.status.state = "failed"
            self.status.error = str(exc) or exc.__class__.__name__
            await self._safe_close(client)
            raise

        self.client = client
        self.status.state = "connected"
        self.status.instructions = client.instructions
        return client

    async def _safe_close(self, client: McpClient) -> None:
        with contextlib.suppress(Exception):
            await client.close()

    async def connect(self) -> None:
        """Connect and list tools. Records the outcome on the status; never raises."""
        if not self.config.enabled:
            self.status.state = "disabled"
            return
        try:
            client = await self.get_client()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.status.state != "needs-auth":
                self.status.state = "failed"
                self.status.error = str(exc) or exc.__class__.__name__
            log.debug("MCP %s did not connect: %s", self.config.name, exc)
            return
        await self.reload_definitions(client)

    async def reconnect(self) -> None:
        """Tear the connection down and build a fresh one.

        Resetting ``_closed`` is the point: after :meth:`close` a connection is
        permanently shut down, so reconnecting has to be a way back rather than
        a no-op.
        """
        await self.close()
        self._closed = False
        self.client = None
        self.tools = []
        self.definitions = []
        self._dispose_tools_changed = None
        self._opening = None
        if not self.config.enabled:
            self.status.state = "disabled"
            return
        self.status.state = "connecting"
        self.status.error = None
        await self.connect()

    async def reload_definitions(self, client: McpClient | None = None) -> list[McpToolDef]:
        """Re-list the server's tools.

        A server that cannot list its tools is still connected -- the resource
        side of the protocol still works -- so this records a note rather than
        failing the connection.
        """
        client = client or self.client
        if client is None:
            return self.definitions
        try:
            self.definitions = await client.list_tools()
        except Exception as exc:
            self.status.error = f"could not list tools: {exc}"
            return self.definitions
        self.status.error = None
        return self.definitions

    def _handle_tools_changed(self, _params: Any) -> None:
        if self._on_change is not None:
            self._on_change(self)

    # ---- tool calls -------------------------------------------------------

    async def call_tool(
        self, name: str, arguments: dict[str, Any], options: McpRequestOptions
    ) -> dict[str, Any]:
        client = await self.get_client()
        try:
            return await client.call_tool(name, arguments, options)
        except McpConnectionClosedError as exc:
            # The connection dropped mid-call. A tool call may already have run
            # server-side, so it is *not* retried; the next call reconnects.
            self.client = None
            self.status.state = "disconnected"
            self.status.error = str(exc)
            raise

    def build_tools(self, is_taken) -> list[McpTool]:
        """Wrap each server tool, resolving name collisions across servers.

        Sanitizing can collide inside one server (``a.b`` and ``a_b`` both
        become ``a_b``), so ``is_taken`` is consulted as tools are built rather
        than relying on the name being unique.
        """
        built: list[McpTool] = []
        for definition in self.definitions:
            tool_name = str(definition.get("name") or "")
            if not tool_name:
                continue
            try:
                built.append(
                    McpTool(
                        server=self.config.name,
                        definition=definition,
                        name=create_mcp_tool_name(self.config.name, tool_name, is_taken),
                        caller=McpToolCaller(server_name=self.config.name, call=self.call_tool),
                        timeout_ms=self.config.timeout_seconds * 1000,
                    )
                )
            except (ValueError, TypeError) as exc:
                # A tool whose input schema we cannot model is skipped with a
                # reason rather than taking the whole server down.
                log.warning("Skipping MCP tool %s/%s: %s", self.config.name, tool_name, exc)
        self.tools = built
        self.status.tool_count = len(built)
        return built

    # ---- teardown ---------------------------------------------------------

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.status.state = "closed"
        if self._dispose_tools_changed is not None:
            with contextlib.suppress(Exception):
                self._dispose_tools_changed()
            self._dispose_tools_changed = None
        if self._opening is not None and not self._opening.done():
            self._opening.cancel()
            with contextlib.suppress(BaseException):
                await self._opening
            self._opening = None
        client, self.client = self.client, None
        if client is not None:
            await self._safe_close(client)


class McpManager:
    """The MCP integration's entry point for the runtime.

    :meth:`connect_all` is bounded by ``startup_wait_seconds`` and never
    raises: a server still connecting at the deadline reports ``connecting``
    and contributes its tools when it lands, because one slow server must not
    hold up a session.
    """

    def __init__(
        self,
        *,
        cwd: str,
        config: LoadedMcpConfig | None = None,
        project_trusted: bool = False,
        config_dir: Path | None = None,
        startup_wait_seconds: float = DEFAULT_STARTUP_WAIT_SECONDS,
    ) -> None:
        self.cwd = cwd
        self.startup_wait_seconds = startup_wait_seconds
        # Kept so reload() re-reads the same files this manager was built from;
        # dropping them would silently switch to the real ~/.vtx/mcp.json.
        self.config_dir = config_dir or get_config_dir()
        self.project_trusted = project_trusted
        self.config = config or load_mcp_config(
            cwd=cwd, project_trusted=project_trusted, config_dir=self.config_dir
        )
        self.errors = list(self.config.errors)
        self.roots: list[Root] = [{"uri": cwd_to_uri(cwd), "name": cwd}]
        self.servers: dict[str, McpServerConnection] = {}
        self._listeners: list[Any] = []
        self._refresh_task: asyncio.Task | None = None
        self._closed = False
        self._build_servers(self.config)

    def _build_servers(self, config: LoadedMcpConfig) -> None:
        for server_config in config.servers:
            self.servers[server_config.name] = McpServerConnection(
                server_config, roots=self.roots, on_change=self._server_changed
            )

    # ---- lifecycle --------------------------------------------------------

    async def connect_all(self) -> list[BaseTool]:
        """Connect every enabled server, bounded, and return the live tools."""
        pending = [c for c in self.servers.values() if c.config.enabled]
        if not pending:
            return []

        tasks = [asyncio.ensure_future(c.connect()) for c in pending]
        _done, not_done = await asyncio.wait(tasks, timeout=self.startup_wait_seconds)
        for task in not_done:
            # Left running: it finishes in the background and the manager is
            # told to rebuild when it does.
            task.add_done_callback(
                lambda t: self._server_changed(None) if not t.cancelled() else None
            )
        return self.rebuild_tools()

    async def reload(self) -> list[BaseTool]:
        """Re-read config, reconnect, and return the new tool list."""
        await self.close()
        self._closed = False
        fresh = load_mcp_config(
            cwd=self.cwd, project_trusted=self.project_trusted, config_dir=self.config_dir
        )
        self.config = fresh
        self.errors = list(fresh.errors)
        self.servers = {}
        self._build_servers(fresh)
        return await self.connect_all()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
            with contextlib.suppress(BaseException):
                await self._refresh_task
            self._refresh_task = None
        for connection in list(self.servers.values()):
            with contextlib.suppress(Exception):
                await connection.close()

    # ---- tools ------------------------------------------------------------

    def rebuild_tools(self) -> list[BaseTool]:
        """Rebuild the tool list from every connected server.

        Rebuilt wholesale rather than merged: a server can rename or drop a
        tool between listings, and a stale entry would call a tool the server
        no longer has. Built-in vtx tool names are reserved first, so an MCP
        tool can never shadow one.
        """
        from vtx.ai.agent.tools import get_all_tools

        taken: set[str] = set(get_all_tools().keys())
        tools: list[BaseTool] = []
        for connection in self.servers.values():
            if connection.status.state != "connected" or connection.client is None:
                connection.tools = []
                continue
            for tool in connection.build_tools(taken.__contains__):
                taken.add(tool.name)
            tools.extend(connection.tools)
        return tools

    def all_tools(self) -> list[BaseTool]:
        return [tool for c in self.servers.values() for tool in c.tools]

    def statuses(self) -> list[McpServerStatus]:
        return [c.status for c in self.servers.values()]

    def get(self, name: str) -> McpServerConnection | None:
        return self.servers.get(name)

    # ---- change notification ---------------------------------------------

    def on_tools_changed(self, callback) -> None:
        """Register a callback for "the tool set changed, re-read it"."""
        self._listeners.append(callback)

    def _server_changed(self, connection: McpServerConnection | None) -> None:
        """A server reported a change; coalesce a rebuild into one task.

        A burst of notifications (or several servers changing at once) must
        produce one rebuild, not one per notification.
        """
        if self._closed:
            return
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_task = asyncio.ensure_future(self._rebuild_and_notify())
        if connection is not None:
            spawn(connection.reload_definitions())

    async def _rebuild_and_notify(self) -> None:
        await asyncio.sleep(0)
        if self._closed:
            return
        self._refresh_task = None
        tools = self.rebuild_tools()
        for listener in list(self._listeners):
            try:
                listener(tools)
            except Exception:
                log.debug("MCP tools-changed listener failed", exc_info=True)


__all__ = [
    "CLIENT_NAME",
    "DEFAULT_STARTUP_WAIT_SECONDS",
    "McpManager",
    "McpServerConnection",
    "McpServerStatus",
    "cwd_to_uri",
]
