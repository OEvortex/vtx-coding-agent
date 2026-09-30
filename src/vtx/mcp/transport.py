"""Transport contract for the MCP client.

A transport owns framing and I/O only. It delivers individual JSON-RPC messages
to :class:`~vtx.mcp.client.McpClient` through the registered listeners; the
client owns request correlation, initialization, timeouts, cancellation, and
server-initiated requests. Keeping that line sharp is what lets the same client
drive a subprocess, an HTTP endpoint, and an in-memory pair.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

from .jsonrpc import JsonRpcMessage

log = logging.getLogger("mcp.transport")

DEFAULT_MAX_MESSAGE_BYTES = 16 * 1024 * 1024

MessageListener = Callable[[JsonRpcMessage], Any]
ErrorListener = Callable[[BaseException], Any]
CloseListener = Callable[[], Any]

# Listener call sites wrap each one in a try/except, so a listener that raises
# is reported and the transport keeps running.


class McpTransport(Protocol):
    """What :class:`~vtx.mcp.client.McpClient` needs from a transport."""

    async def start(self) -> None: ...

    async def send(self, message: JsonRpcMessage) -> None: ...

    async def close(self) -> None: ...

    def on_message(self, listener: MessageListener) -> Callable[[], None]: ...

    def on_error(self, listener: ErrorListener) -> Callable[[], None]: ...

    def on_close(self, listener: CloseListener) -> Callable[[], None]: ...

    def set_protocol_version(self, version: str) -> None:
        """Tells the transport which version was negotiated.

        HTTP transports echo it back in ``MCP-Protocol-Version``. Optional:
        transports that have no use for it do not implement it.
        """


class TransportEvents:
    """Listener bookkeeping shared by every transport.

    Subclasses call :meth:`emit_message`, :meth:`emit_error`, and
    :meth:`emit_close`; :meth:`emit_close` fires at most once per transport.
    """

    def __init__(self) -> None:
        self._message_listeners: list[MessageListener] = []
        self._error_listeners: list[ErrorListener] = []
        self._close_listeners: list[CloseListener] = []
        self._close_emitted = False

    def on_message(self, listener: MessageListener) -> Callable[[], None]:
        self._message_listeners.append(listener)

        def dispose() -> None:
            if listener in self._message_listeners:
                self._message_listeners.remove(listener)

        return dispose

    def on_error(self, listener: ErrorListener) -> Callable[[], None]:
        self._error_listeners.append(listener)

        def dispose() -> None:
            if listener in self._error_listeners:
                self._error_listeners.remove(listener)

        return dispose

    def on_close(self, listener: CloseListener) -> Callable[[], None]:
        self._close_listeners.append(listener)

        def dispose() -> None:
            if listener in self._close_listeners:
                self._close_listeners.remove(listener)

        return dispose

    def emit_message(self, message: JsonRpcMessage) -> None:
        for listener in list(self._message_listeners):
            try:
                listener(message)
            except Exception as exc:  # a bad listener must not kill the transport
                log.debug("MCP message listener failed", exc_info=exc)

    def emit_error(self, error: BaseException) -> None:
        for listener in list(self._error_listeners):
            try:
                listener(error)
            except Exception as exc:
                log.debug("MCP error listener failed", exc_info=exc)

    def emit_close(self) -> None:
        if self._close_emitted:
            return
        self._close_emitted = True
        for listener in list(self._close_listeners):
            try:
                listener()
            except Exception as exc:
                log.debug("MCP close listener failed", exc_info=exc)


__all__ = [
    "DEFAULT_MAX_MESSAGE_BYTES",
    "CloseListener",
    "ErrorListener",
    "McpTransport",
    "MessageListener",
    "TransportEvents",
]
