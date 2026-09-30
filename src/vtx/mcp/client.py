"""Transport-neutral MCP client.

Owns request correlation, the ``initialize`` handshake, timeouts, cancellation,
server-initiated requests, and the paginated list helpers. Knows nothing about
stdio, HTTP, or OAuth -- it only talks to an :class:`McpTransport`.

Cancellation is an ``asyncio.Event`` rather than an abort signal, which is the
same shape :meth:`vtx.ai.agent.tools.base.BaseTool.execute` already takes, so a
tool call can hand the agent's interrupt straight through.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .jsonrpc import (
    JSON_RPC_INTERNAL_ERROR,
    JSON_RPC_METHOD_NOT_FOUND,
    JsonRpcId,
    JsonRpcMessage,
    McpAbortError,
    McpConnectionClosedError,
    McpError,
    McpTimeoutError,
    is_jsonrpc_id,
    is_jsonrpc_notification,
    is_jsonrpc_request,
    is_jsonrpc_response,
    is_object,
)
from .tasks import spawn
from .transport import McpTransport
from .types import (
    LATEST_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    CallToolResult,
    ClientCapabilities,
    Implementation,
    InitializeResult,
    ReadResourceResult,
    Resource,
    ResourceListItem,
    ResourceTemplate,
    ResourceTemplateListItem,
    Root,
    ServerCapabilities,
    Tool,
    ToolListItem,
    normalize_resource,
    normalize_resource_template,
    validate_call_tool_result,
    validate_initialize_result,
    validate_list_page,
    validate_read_resource_result,
)

log = logging.getLogger("mcp.client")

DEFAULT_REQUEST_TIMEOUT_MS = 30_000
MAX_LIST_PAGES = 1_000

NotificationListener = Callable[[Any], Any]
ErrorListener = Callable[[Exception], Any]
CloseListener = Callable[[], Any]
ProgressListener = Callable[[dict[str, Any]], Any]
RequestHandler = Callable[[Any, asyncio.Event], Awaitable[Any] | Any]


@dataclass
class McpClientOptions:
    name: str
    version: str
    title: str | None = None
    capabilities: ClientCapabilities | None = None
    protocol_version: str | None = None
    request_timeout_ms: int | None = None
    roots: list[Root] | Callable[[], Any] | None = None
    """Static roots, or a callable returning them (possibly a coroutine).

    A callable is re-read on every ``roots/list`` so a server that asks later
    sees the roots that exist now, not the ones that existed at connect time.
    """


@dataclass
class McpRequestOptions:
    """Per-request overrides.

    ``cancel_event`` is the caller's interrupt. Setting it rejects the request
    with :class:`McpAbortError` and sends ``notifications/cancelled``.
    ``on_progress`` attaches a progress token to the request and re-arms the
    timeout on every notification, so a slow-but-alive server is not killed.
    """

    cancel_event: asyncio.Event | None = None
    timeout_ms: int | None = None
    on_progress: ProgressListener | None = None


@dataclass
class _PendingRequest:
    future: asyncio.Future
    timeout_ms: int
    timer: asyncio.TimerHandle | None = None
    cancel_waiter: asyncio.Task | None = None
    on_progress: ProgressListener | None = None
    progress_token: JsonRpcId | None = None
    method: str = ""
    cancellable: bool = True
    # Only set for a notification-driven timeout, so the two teardown paths can
    # produce different reasons.
    notify_reason: str | None = None


@dataclass
class _ServerRequest:
    event: asyncio.Event
    reason: str = ""


class McpClient:
    """An MCP client bound to one transport at a time."""

    def __init__(self, options: McpClientOptions) -> None:
        self.options = options
        self._state = "idle"
        self._transport: McpTransport | None = None
        self._disposers: list[Callable[[], None]] = []
        self._next_request_id = 1

        self._server_info: Implementation | None = None
        self._server_capabilities: ServerCapabilities | None = None
        self._instructions: str | None = None
        self._protocol_version: str | None = None

        self._pending: dict[JsonRpcId, _PendingRequest] = {}
        self._progress_requests: dict[JsonRpcId, JsonRpcId] = {}
        self._incoming: dict[JsonRpcId, _ServerRequest] = {}
        self._request_handlers: dict[str, RequestHandler] = {}
        self._notification_listeners: dict[str, list[NotificationListener]] = {}
        self._error_listeners: list[ErrorListener] = []
        self._close_listeners: list[CloseListener] = []

        # Every MCP client answers ping; answering it is required, not optional.
        self._request_handlers["ping"] = lambda _params, _event: {}
        if options.roots is not None:
            self._install_roots_handler()

    # ---- introspection ---------------------------------------------------

    @property
    def connection_state(self) -> str:
        return self._state

    @property
    def connected(self) -> bool:
        return self._state == "connected"

    @property
    def server_info(self) -> Implementation | None:
        return self._server_info

    @property
    def server_capabilities(self) -> ServerCapabilities | None:
        return self._server_capabilities

    @property
    def instructions(self) -> str | None:
        return self._instructions

    @property
    def protocol_version(self) -> str | None:
        return self._protocol_version

    # ---- lifecycle -------------------------------------------------------

    async def connect(self, transport: McpTransport) -> InitializeResult:
        """Start the transport and run the ``initialize`` handshake."""
        if self._state != "idle":
            raise RuntimeError(f"Cannot connect MCP client in {self._state} state")
        self._state = "connecting"
        self._transport = transport
        self._disposers = [
            transport.on_message(self._handle_message),
            # Transport errors are reported only. Pending requests fail when
            # the transport closes, which carries a better reason than
            # "connection reset" would.
            transport.on_error(self._emit_error),
            transport.on_close(self._handle_transport_close),
        ]

        try:
            await transport.start()
            capabilities: dict[str, Any] = dict(self.options.capabilities or {})
            if self.options.roots is not None and "roots" not in capabilities:
                capabilities["roots"] = {}
            client_info: dict[str, Any] = {
                "name": self.options.name,
                "version": self.options.version,
            }
            if self.options.title is not None:
                client_info["title"] = self.options.title

            result = validate_initialize_result(
                await self._request(
                    "initialize",
                    {
                        "protocolVersion": self.options.protocol_version
                        or LATEST_PROTOCOL_VERSION,
                        "capabilities": capabilities,
                        "clientInfo": client_info,
                    },
                    McpRequestOptions(),
                    allow_connecting=True,
                )
            )
            if result["protocolVersion"] not in SUPPORTED_PROTOCOL_VERSIONS:
                raise RuntimeError(
                    f"MCP server selected unsupported protocol version {result['protocolVersion']}"
                )

            self._protocol_version = result["protocolVersion"]
            self._server_info = result["serverInfo"]
            self._server_capabilities = result["capabilities"]
            self._instructions = result.get("instructions")
            with contextlib.suppress(Exception):
                transport.set_protocol_version(result["protocolVersion"])
            await self._notify("notifications/initialized", None, allow_connecting=True)
            self._state = "connected"
            return result
        except BaseException:
            # Includes CancelledError: a half-open transport must not survive a
            # cancelled connect.
            with contextlib.suppress(Exception):
                await self.close()
            raise

    async def close(self) -> None:
        transport = self._transport
        self._transport = None
        self._dispose_transport_listeners()
        self._mark_closed(McpConnectionClosedError())
        if transport is not None:
            with contextlib.suppress(Exception):
                await transport.close()

    # ---- requests --------------------------------------------------------

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        options: McpRequestOptions | None = None,
    ) -> Any:
        return await self._request(
            method, params, options or McpRequestOptions(), allow_connecting=False
        )

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._notify(method, params, allow_connecting=False)

    async def _request(
        self,
        method: str,
        params: dict[str, Any] | None,
        options: McpRequestOptions,
        *,
        allow_connecting: bool,
    ) -> Any:
        transport = self._require_transport(allow_connecting)
        if options.cancel_event is not None and options.cancel_event.is_set():
            raise McpAbortError()

        request_id = self._next_request_id
        self._next_request_id += 1

        progress_token = request_id if options.on_progress is not None else None
        request_params = params
        if progress_token is not None:
            meta = params.get("_meta") if params and is_object(params.get("_meta")) else {}
            request_params = {
                **(params or {}),
                "_meta": {**(meta or {}), "progressToken": progress_token},
            }

        message: JsonRpcMessage = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if request_params is not None:
            message["params"] = request_params

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        # The spec forbids cancelling `initialize`, so a timeout there rejects
        # locally without telling the server to stop.
        cancellable = method != "initialize"
        entry = _PendingRequest(
            future=future,
            timeout_ms=options.timeout_ms
            or self.options.request_timeout_ms
            or DEFAULT_REQUEST_TIMEOUT_MS,
            on_progress=options.on_progress,
            progress_token=progress_token,
            method=method,
            cancellable=cancellable,
        )
        self._pending[request_id] = entry
        if progress_token is not None:
            self._progress_requests[progress_token] = request_id

        if options.cancel_event is not None:
            entry.cancel_waiter = asyncio.ensure_future(
                self._watch_cancel(request_id, options.cancel_event, cancellable)
            )
        self._arm_timeout(request_id, entry)

        try:
            await transport.send(message)
        except BaseException as exc:
            self._cancel_pending(request_id, exc, notify_server=False)
        return await future

    async def _watch_cancel(
        self, request_id: JsonRpcId, cancel_event: asyncio.Event, cancellable: bool
    ) -> None:
        await cancel_event.wait()
        reason = "Cancelled"
        self._cancel_pending(
            request_id, McpAbortError(reason), notify_server=cancellable, reason=reason
        )

    async def _notify(
        self, method: str, params: dict[str, Any] | None, *, allow_connecting: bool
    ) -> None:
        transport = self._require_transport(allow_connecting)
        message: JsonRpcMessage = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        await transport.send(message)

    def _require_transport(self, allow_connecting: bool) -> McpTransport:
        transport = self._transport
        if transport is not None and (
            self._state == "connected" or (allow_connecting and self._state == "connecting")
        ):
            return transport
        raise McpConnectionClosedError(f"MCP client is {self._state}")

    # ---- incoming messages -----------------------------------------------

    def _handle_message(self, message: JsonRpcMessage) -> None:
        if is_jsonrpc_response(message):
            self._handle_response(message)
            return
        if is_jsonrpc_request(message):
            # spawn, not a bare ensure_future: the loop holds only a weak
            # reference to a running task, so an unreferenced one can be
            # collected mid-handler. This one handles its own errors.
            spawn(self._handle_request(message))
            return
        if is_jsonrpc_notification(message):
            self._handle_notification(message["method"], message.get("params"))
            return
        self._emit_error(McpError(-32600, "Received invalid JSON-RPC message"))

    def _handle_response(self, message: JsonRpcMessage) -> None:
        entry = self._pending.get(message["id"])
        if entry is None:
            self._emit_error(
                Exception(f"Received response for unknown MCP request {message['id']}")
            )
            return
        self._remove_pending(message["id"], entry)
        error = message.get("error")
        if is_object(error):
            entry.future.set_exception(
                McpError(
                    error.get("code", JSON_RPC_INTERNAL_ERROR),
                    error.get("message", ""),
                    error.get("data"),
                )
            )
        elif not entry.future.done():
            entry.future.set_result(message.get("result"))

    async def _handle_request(self, message: JsonRpcMessage) -> None:
        transport = self._transport
        if transport is None:
            return
        handler = self._request_handlers.get(message["method"])
        if handler is None:
            with contextlib.suppress(Exception):
                await transport.send(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {
                            "code": JSON_RPC_METHOD_NOT_FOUND,
                            "message": f"Method not found: {message['method']}",
                        },
                    }
                )
            return

        incoming = _ServerRequest(event=asyncio.Event())
        self._incoming[message["id"]] = incoming
        try:
            result = handler(message.get("params"), incoming.event)
            if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
                result = await result
            with contextlib.suppress(McpConnectionClosedError, Exception):
                await transport.send(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": result if result is not None else {},
                    }
                )
        except Exception as exc:
            if isinstance(exc, McpError):
                error = {
                    "code": exc.code,
                    "message": str(exc.args[0] if exc.args else exc),
                    "data": exc.data,
                }
            else:
                error = {"code": JSON_RPC_INTERNAL_ERROR, "message": str(exc)}
            with contextlib.suppress(Exception):
                await transport.send({"jsonrpc": "2.0", "id": message["id"], "error": error})
        finally:
            self._incoming.pop(message["id"], None)

    def _handle_notification(self, method: str, params: Any) -> None:
        if method == "notifications/progress":
            self._handle_progress(params)
        elif method == "notifications/cancelled":
            self._handle_cancelled(params)
        for listener in list(self._notification_listeners.get(method, ())):
            try:
                listener(params)
            except Exception as exc:
                self._emit_error(exc)

    def _handle_progress(self, params: Any) -> None:
        if not is_object(params) or not is_jsonrpc_id(params.get("progressToken")):
            return
        if not isinstance(params.get("progress"), (int, float)) or isinstance(
            params.get("progress"), bool
        ):
            return
        token = params["progressToken"]
        request_id = self._progress_requests.get(token)
        entry = self._pending.get(request_id) if request_id is not None else None
        if entry is None:
            return
        # Progress proves the server is alive, so the deadline moves.
        self._arm_timeout(request_id, entry)
        if entry.on_progress is not None:
            try:
                entry.on_progress(params)
            except Exception as exc:
                self._emit_error(exc)

    def _handle_cancelled(self, params: Any) -> None:
        if not is_object(params) or not is_jsonrpc_id(params.get("requestId")):
            return
        incoming = self._incoming.get(params["requestId"])
        if incoming is None:
            return
        reason = params.get("reason")
        incoming.reason = reason if isinstance(reason, str) else ""
        incoming.event.set()

    # ---- timeouts and teardown -------------------------------------------

    def _arm_timeout(self, request_id: JsonRpcId, entry: _PendingRequest) -> None:
        if entry.timer is not None:
            entry.timer.cancel()
            entry.timer = None
        if entry.timeout_ms <= 0:
            return
        loop = asyncio.get_running_loop()
        entry.timer = loop.call_later(
            entry.timeout_ms / 1000,
            self._on_timeout,
            request_id,
            entry.timeout_ms,
            entry.cancellable,
        )

    def _on_timeout(self, request_id: JsonRpcId, timeout_ms: int, cancellable: bool) -> None:
        self._cancel_pending(
            request_id,
            McpTimeoutError(timeout_ms),
            notify_server=cancellable,
            reason="Request timed out",
        )

    def _cancel_pending(
        self,
        request_id: JsonRpcId,
        error: BaseException,
        *,
        notify_server: bool,
        reason: str | None = None,
    ) -> None:
        entry = self._pending.get(request_id)
        if entry is None:
            return
        self._remove_pending(request_id, entry)
        if not entry.future.done():
            entry.future.set_exception(error)
        if notify_server and self._transport is not None:
            params: dict[str, Any] = {"requestId": request_id}
            if reason:
                params["reason"] = reason
            spawn(self._send_cancelled(params))

    async def _send_cancelled(self, params: dict[str, Any]) -> None:
        try:
            await self._notify("notifications/cancelled", params, allow_connecting=False)
        except Exception as exc:
            self._emit_error(exc)

    def _remove_pending(self, request_id: JsonRpcId, entry: _PendingRequest) -> None:
        self._pending.pop(request_id, None)
        if entry.timer is not None:
            entry.timer.cancel()
            entry.timer = None
        if entry.cancel_waiter is not None:
            entry.cancel_waiter.cancel()
            entry.cancel_waiter = None
        if entry.progress_token is not None:
            self._progress_requests.pop(entry.progress_token, None)

    def _reject_pending(self, error: BaseException) -> None:
        for request_id, entry in list(self._pending.items()):
            self._remove_pending(request_id, entry)
            if not entry.future.done():
                entry.future.set_exception(error)

    def _handle_transport_close(self) -> None:
        self._mark_closed(McpConnectionClosedError())

    def _mark_closed(self, error: BaseException) -> None:
        was_closed = self._state == "closed"
        self._state = "closed"
        self._reject_pending(error)
        for incoming in list(self._incoming.values()):
            incoming.reason = str(error)
            incoming.event.set()
        self._incoming.clear()
        if was_closed:
            return
        for listener in list(self._close_listeners):
            try:
                listener()
            except Exception as exc:
                self._emit_error(exc)

    # ---- listener registration -------------------------------------------

    def set_request_handler(self, method: str, handler: RequestHandler) -> Callable[[], None]:
        self._request_handlers[method] = handler

        def dispose() -> None:
            if self._request_handlers.get(method) is handler:
                del self._request_handlers[method]

        return dispose

    def on_notification(self, method: str, listener: NotificationListener) -> Callable[[], None]:
        self._notification_listeners.setdefault(method, []).append(listener)

        def dispose() -> None:
            listeners = self._notification_listeners.get(method)
            if listeners and listener in listeners:
                listeners.remove(listener)
                if not listeners:
                    del self._notification_listeners[method]

        return dispose

    def on_error(self, listener: ErrorListener) -> Callable[[], None]:
        self._error_listeners.append(listener)

        def dispose() -> None:
            if listener in self._error_listeners:
                self._error_listeners.remove(listener)

        return dispose

    def on_close(self, listener: CloseListener) -> Callable[[], None]:
        """Fires once, whether the transport dropped or :meth:`close` ran."""
        self._close_listeners.append(listener)

        def dispose() -> None:
            if listener in self._close_listeners:
                self._close_listeners.remove(listener)

        return dispose

    def _emit_error(self, error: BaseException) -> None:
        for listener in list(self._error_listeners):
            try:
                listener(error)
            except Exception:
                log.debug("MCP error listener raised", exc_info=True)

    def _dispose_transport_listeners(self) -> None:
        for dispose in self._disposers:
            with contextlib.suppress(Exception):
                dispose()
        self._disposers = []

    # ---- protocol helpers ------------------------------------------------

    async def ping(self, options: McpRequestOptions | None = None) -> None:
        await self.request("ping", None, options or McpRequestOptions())

    async def list_tools(self, options: McpRequestOptions | None = None) -> list[Tool]:
        items = await self._list_all(
            "tools/list", "tools", ToolListItem, options or McpRequestOptions()
        )
        return items  # ty: ignore[invalid-return-type]

    async def list_resources(self, options: McpRequestOptions | None = None) -> list[Resource]:
        items = await self._list_all(
            "resources/list", "resources", ResourceListItem, options or McpRequestOptions()
        )
        return [normalize_resource(item) for item in items]  # ty: ignore[invalid-return-type]

    async def list_resources_page(
        self, cursor: str | None = None, options: McpRequestOptions | None = None
    ) -> dict[str, Any]:
        items, next_cursor = await self._list_page(
            "resources/list", "resources", ResourceListItem, cursor, options or McpRequestOptions()
        )
        page: dict[str, Any] = {"resources": [normalize_resource(item) for item in items]}
        if next_cursor is not None:
            page["nextCursor"] = next_cursor
        return page

    async def list_resource_templates(
        self, options: McpRequestOptions | None = None
    ) -> list[ResourceTemplate]:
        items = await self._list_all(
            "resources/templates/list",
            "resourceTemplates",
            ResourceTemplateListItem,
            options or McpRequestOptions(),
        )
        return [normalize_resource_template(item) for item in items]  # ty: ignore[invalid-return-type]

    async def list_resource_templates_page(
        self, cursor: str | None = None, options: McpRequestOptions | None = None
    ) -> dict[str, Any]:
        items, next_cursor = await self._list_page(
            "resources/templates/list",
            "resourceTemplates",
            ResourceTemplateListItem,
            cursor,
            options or McpRequestOptions(),
        )
        page: dict[str, Any] = {
            "resourceTemplates": [normalize_resource_template(item) for item in items]
        }
        if next_cursor is not None:
            page["nextCursor"] = next_cursor
        return page

    async def read_resource(
        self, uri: str, options: McpRequestOptions | None = None
    ) -> ReadResourceResult:
        return validate_read_resource_result(
            await self.request("resources/read", {"uri": uri}, options or McpRequestOptions())
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        options: McpRequestOptions | None = None,
    ) -> CallToolResult:
        params: dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return validate_call_tool_result(
            await self.request("tools/call", params, options or McpRequestOptions())
        )

    async def _list_page(
        self,
        method: str,
        key: str,
        is_item: Callable[[dict[str, Any]], bool],
        cursor: str | None,
        options: McpRequestOptions,
    ) -> tuple[list[dict[str, Any]], str | None]:
        params = {"cursor": cursor} if cursor is not None else None
        return validate_list_page(
            method, key, await self.request(method, params, options), is_item
        )

    async def _list_all(
        self,
        method: str,
        key: str,
        is_item: Callable[[dict[str, Any]], bool],
        options: McpRequestOptions,
    ) -> list[dict[str, Any]]:
        """Follow ``nextCursor`` through every page.

        Two failure modes are handled explicitly because both hang or loop
        forever otherwise: a server that keeps handing back a cursor it has
        already used, and one that paginates without end.
        """
        items: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        cursor: str | None = None
        for _ in range(MAX_LIST_PAGES):
            page_items, next_cursor = await self._list_page(method, key, is_item, cursor, options)
            items.extend(page_items)
            if next_cursor is None:
                return items
            if next_cursor in seen_cursors:
                raise RuntimeError(f"MCP {method} returned duplicate cursor: {next_cursor}")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise RuntimeError(f"MCP {method} exceeded {MAX_LIST_PAGES} pages")

    # ---- roots -----------------------------------------------------------

    def _install_roots_handler(self) -> None:
        roots = self.options.roots
        if roots is None:
            return

        async def handle(_params: Any, _event: asyncio.Event) -> dict[str, Any]:
            resolved = roots() if callable(roots) else roots
            if asyncio.iscoroutine(resolved):
                resolved = await resolved
            return {"roots": list(resolved or [])}

        self._request_handlers["roots/list"] = handle


__all__ = [
    "DEFAULT_REQUEST_TIMEOUT_MS",
    "MAX_LIST_PAGES",
    "McpClient",
    "McpClientOptions",
    "McpRequestOptions",
]
