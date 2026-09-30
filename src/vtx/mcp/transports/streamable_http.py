"""Streamable HTTP transport (MCP spec 2025-03-26 and later).

Client requests are POSTs that the server may answer with a JSON body or with
an SSE stream. The server may also push its own requests and notifications down
a GET stream opened after initialization, and may assign SSE event IDs that let
a dropped stream be resumed with ``Last-Event-ID``.

One httpx detail drives most of this file's shape: a response whose body is an
SSE stream is fetched with ``client.send(..., stream=True)`` and closed
explicitly, because leaving it inside an ``async with`` would close the stream
the moment ``send()`` returns -- and ``send()`` returns long before the server
answers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..auth import AuthProvider, UnauthorizedContext
from ..jsonrpc import (
    JSON_RPC_INTERNAL_ERROR,
    JsonRpcId,
    JsonRpcMessage,
    McpConnectionClosedError,
    is_jsonrpc_request,
    is_jsonrpc_response,
    parse_jsonrpc_message,
)
from ..transport import DEFAULT_MAX_MESSAGE_BYTES, TransportEvents

log = logging.getLogger("mcp.transport.http")

MAX_ERROR_BODY_BYTES = 8 * 1024
ERROR_MESSAGE_BODY_CHARS = 500
DEFAULT_RECONNECT_INITIAL_DELAY_MS = 1_000
DEFAULT_RECONNECT_MAX_DELAY_MS = 30_000
DEFAULT_RECONNECT_MAX_RETRIES = 5

# A stream must not be cut off by httpx's default 5s read timeout, so the
# transport is built with no read timeout and bounds each request itself.
STREAM_TIMEOUT = httpx.Timeout(connect=30.0, read=None, write=30.0, pool=30.0)


class McpHttpError(Exception):
    def __init__(self, status: int, message: str, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class McpAuthRequiredError(McpHttpError):
    """The server answered 401 and no provider supplied usable credentials."""

    def __init__(self, www_authenticate: str | None, body: str = "") -> None:
        super().__init__(401, "MCP server requires authentication", body)
        self.www_authenticate = www_authenticate


class McpSessionExpiredError(McpHttpError):
    """The server no longer knows a session we were using (restart or deploy).

    The request provably did not run, so the caller can retry on a fresh
    session -- which is why this is distinct from a generic 404.
    """

    def __init__(self, body: str = "") -> None:
        super().__init__(404, "MCP session expired", body)


@dataclass
class SseEvent:
    event: str | None = None
    data: str = ""
    id: str | None = None


@dataclass
class ReconnectOptions:
    initial_delay_ms: int = DEFAULT_RECONNECT_INITIAL_DELAY_MS
    max_delay_ms: int = DEFAULT_RECONNECT_MAX_DELAY_MS
    max_retries: int = DEFAULT_RECONNECT_MAX_RETRIES


@dataclass
class StreamableHttpTransportOptions:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    client: httpx.AsyncClient | None = None
    """Injected for proxying, custom TLS, or tests. One is created if absent."""
    open_get_stream: bool = True
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES
    auth_provider: AuthProvider | None = None
    reconnect: ReconnectOptions | None = None


@dataclass
class _StreamCursor:
    last_event_id: str | None = None
    retry_ms: int | None = None
    received: bool = False


def _content_type(response: httpx.Response) -> str | None:
    raw = response.headers.get("content-type")
    if not raw:
        return None
    return raw.split(";", 1)[0].strip().lower()


def _needs_authorization(response: httpx.Response) -> bool:
    """401, or 403 with an ``insufficient_scope`` bearer challenge.

    The 403 case is step-up authorization: the token is valid but lacks the
    scope the request needs, and refreshing will not add it.
    """
    if response.status_code == 401:
        return True
    if response.status_code != 403:
        return False
    challenge = response.headers.get("www-authenticate") or ""
    return (
        re.search(r'(?:^|[\s,])error="?insufficient_scope"?', challenge, re.IGNORECASE) is not None
    )


def _is_transient_status(status: int) -> bool:
    return status in (408, 429) or status >= 500


def _describe_failure(status: int, body: str) -> str:
    text = body.strip()
    if len(text) > ERROR_MESSAGE_BODY_CHARS:
        text = text[: ERROR_MESSAGE_BODY_CHARS - 3] + "..."
    return f"MCP HTTP request failed with status {status}" + (f": {text}" if text else "")


class StreamableHttpTransport(TransportEvents):
    def __init__(self, options: StreamableHttpTransportOptions) -> None:
        super().__init__()
        self.options = options
        self.url = options.url
        self._client = options.client
        self._owns_client = options.client is None
        self._started = False
        self._closed = False
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._get_stream_started = False
        self._open_streams: set[httpx.Response] = set()
        self._sleep_task: asyncio.Task | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=STREAM_TIMEOUT, follow_redirects=True)
        return self._client

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("MCP Streamable HTTP transport already started")
        if self._closed:
            raise McpConnectionClosedError()
        self._started = True

    def set_protocol_version(self, version: str) -> None:
        self._protocol_version = version

    async def send(self, message: JsonRpcMessage) -> None:
        if not self._started or self._closed:
            raise McpConnectionClosedError()
        response = await self._authorized_request(
            "POST",
            headers={
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
            },
            content=json.dumps(message).encode("utf-8"),
        )
        await self._check_response(response)
        self._capture_session(response)

        if not is_jsonrpc_request(message):
            # Notifications and responses are acknowledged with 202 and carry no
            # reply, so any body is noise.
            await response.aclose()
            # The server-to-client stream may only open once the session exists.
            if message.get("method") == "notifications/initialized":
                self._start_get_stream()
            return

        if response.status_code in (202, 204):
            await response.aclose()
            raise McpHttpError(
                response.status_code,
                f"MCP server accepted request {message.get('method')} without a response",
            )

        content_type = _content_type(response)
        if content_type == "application/json":
            body = await response.aread()
            await response.aclose()
            parsed = json.loads(body)
            for item in parsed if isinstance(parsed, list) else [parsed]:
                self.emit_message(parse_jsonrpc_message(item))
            return

        if content_type == "text/event-stream":
            # Left open on purpose: the SSE reader task owns it from here and
            # closes it when the stream ends.
            asyncio.ensure_future(self._consume_response_stream(response, message["id"]))
            return

        await response.aclose()
        raise McpHttpError(
            response.status_code,
            f"Unsupported MCP response content type: {content_type or 'missing'}",
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._sleep_task is not None:
            self._sleep_task.cancel()
            self._sleep_task = None

        # End the session server-side so it can release per-session state,
        # then drop our streams and the client we own.
        if self._started and self._session_id and self._client is not None:
            headers, _token = await self._headers()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    self._client.delete(self.url, headers=headers),
                    timeout=1.0,
                )
        for response in list(self._open_streams):
            with contextlib.suppress(Exception):
                await response.aclose()
        self._open_streams.clear()
        if self._owns_client and self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
            self._client = None
        self.emit_close()

    # ---- request helpers -------------------------------------------------

    async def _authorized_request(
        self, method: str, *, headers: dict[str, str], content: bytes | None = None
    ) -> httpx.Response:
        """Request with auth headers, retrying once through ``on_unauthorized``.

        Only the first attempt consults the provider: if a refresh did not fix
        the request, trying again unboundedly turns a broken credential into a
        hang.
        """
        provider = self.options.auth_provider
        for attempt in range(2):
            request_headers, token = await self._headers(headers)
            # stream=True is essential, not an optimisation: client.request()
            # buffers the whole body, and an SSE response deliberately stays
            # open, so it would block until the server closed it -- forever.
            # The caller decides when to read (aread for a JSON reply) or to
            # hand the live stream to a reader task that closes it on the end.
            request = self._get_client().build_request(
                method, self.url, headers=request_headers, content=content
            )
            response = await self._get_client().send(request, stream=True)
            if attempt > 0 or provider is None or not _needs_authorization(response):
                return response
            try:
                await provider.on_unauthorized(
                    UnauthorizedContext(
                        response=response,
                        server_url=self.url,
                        token=token,
                        fetch=self._get_client(),
                    )
                )
            finally:
                with contextlib.suppress(Exception):
                    await response.aclose()
        raise McpHttpError(401, "MCP authorization retry exhausted")

    async def _headers(
        self, extra: dict[str, str] | None = None
    ) -> tuple[dict[str, str], str | None]:
        headers = dict(self.options.headers)
        if extra:
            headers.update(extra)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        token: str | None = None
        if self.options.auth_provider is not None:
            token = await self.options.auth_provider.token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers, token

    def _capture_session(self, response: httpx.Response) -> None:
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id

    async def _check_response(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        # A body we cannot read is not a reason to lose the status code, which
        # is what the caller actually branches on.
        body = ""
        with contextlib.suppress(Exception):
            body = (await response.aread()).decode("utf-8", "replace")[:MAX_ERROR_BODY_BYTES]
        if response.status_code == 401:
            raise McpAuthRequiredError(response.headers.get("www-authenticate"), body)
        if response.status_code == 404 and self._session_id:
            raise McpSessionExpiredError(body)
        raise McpHttpError(
            response.status_code, _describe_failure(response.status_code, body), body
        )

    # ---- SSE -------------------------------------------------------------

    async def _consume_sse(
        self, response: httpx.Response, cursor: _StreamCursor, on_message: Any = None
    ) -> None:
        """Read one SSE stream to completion, dispatching JSON-RPC events."""
        self._open_streams.add(response)
        try:
            async for event in self._iter_sse(response, self.options.max_message_bytes, cursor):
                cursor.last_event_id = event.id or cursor.last_event_id
                if not event.data.strip():
                    # An id with no data primes resumption; it is not a message.
                    continue
                if event.event is not None and event.event != "message":
                    continue
                try:
                    message = parse_jsonrpc_message(json.loads(event.data))
                except Exception as exc:
                    self.emit_error(exc)
                    continue
                if on_message is not None:
                    on_message(message)
                self.emit_message(message)
                cursor.received = True
        finally:
            self._open_streams.discard(response)
            with contextlib.suppress(Exception):
                await response.aclose()

    async def _iter_sse(
        self, response: httpx.Response, max_event_bytes: int, cursor: _StreamCursor | None = None
    ):
        """Yield :class:`SseEvent` from an SSE byte stream.

        Tracks the byte size of the *pending* event's data, not just the
        buffer: a server streaming many short ``data:`` lines with no
        terminating blank line would otherwise grow without bound.
        """
        buffered = ""
        event_name: str | None = None
        event_id: str | None = None
        data_lines: list[str] = []
        data_bytes = 0
        async for raw in response.aiter_text():
            buffered += raw
            while "\n" in buffered:
                line, buffered = buffered.split("\n", 1)
                dispatched, event_name, event_id, data_lines, data_bytes = self._sse_line(
                    line.rstrip("\r"),
                    event_name,
                    event_id,
                    data_lines,
                    data_bytes,
                    max_event_bytes,
                    cursor,
                )
                if dispatched is not None:
                    yield dispatched
            if len(buffered.encode("utf-8", "replace")) > max_event_bytes:
                raise ValueError(f"MCP SSE event exceeds {max_event_bytes} bytes")
        if buffered:
            dispatched, event_name, event_id, data_lines, data_bytes = self._sse_line(
                buffered.rstrip("\r"),
                event_name,
                event_id,
                data_lines,
                data_bytes,
                max_event_bytes,
                cursor,
            )
            if dispatched is not None:
                yield dispatched
        if data_lines:
            yield SseEvent(event=event_name, data="\n".join(data_lines), id=event_id)

    def _sse_line(
        self,
        line: str,
        event_name: str | None,
        event_id: str | None,
        data_lines: list[str],
        data_bytes: int,
        max_event_bytes: int,
        cursor: _StreamCursor | None = None,
    ) -> tuple[SseEvent | None, str | None, str | None, list[str], int]:
        """Process one SSE line. Returns the dispatched event, if any."""
        if line == "":
            if not data_lines:
                return None, None, None, [], 0
            event = SseEvent(event=event_name, data="\n".join(data_lines), id=event_id)
            return event, None, None, [], 0
        if line.startswith(":"):
            # A comment, commonly a keepalive.
            return None, event_name, event_id, data_lines, data_bytes
        colon = line.find(":")
        field = line if colon < 0 else line[:colon]
        value = "" if colon < 0 else line[colon + 1 :]
        if value.startswith(" "):
            value = value[1:]

        if field == "data":
            data_bytes += len(value.encode("utf-8", "replace")) + (1 if data_lines else 0)
            if data_bytes > max_event_bytes:
                raise ValueError(f"MCP SSE event exceeds {max_event_bytes} bytes")
            data_lines.append(value)
        elif field == "event":
            event_name = value
        elif field == "id" and "\0" not in value:
            event_id = value
            if cursor is not None:
                # Recorded as soon as the line is seen, not when the event is
                # dispatched. A priming event carries an id and no data, and it
                # exists precisely so a dropped stream can be resumed -- waiting
                # for a dispatch would mean never learning the id at all.
                cursor.last_event_id = value
        elif field == "retry" and value.isdigit():
            # A server-supplied reconnect delay overrides our backoff, so it is
            # read here rather than at the reconnect decision.
            if cursor is not None:
                cursor.retry_ms = int(value)
        return None, event_name, event_id, data_lines, data_bytes

    async def _consume_response_stream(
        self, response: httpx.Response, request_id: JsonRpcId
    ) -> None:
        """Read the stream answering one request, resuming it if it drops.

        Servers close response streams at will, so a stream that ends before
        the response arrives is resumed with GET + ``Last-Event-ID`` when the
        server assigned event IDs. Without IDs only this request fails.
        """
        cursor = _StreamCursor()
        answered = False

        def on_message(message: JsonRpcMessage) -> None:
            nonlocal answered
            if is_jsonrpc_response(message) and message.get("id") == request_id:
                answered = True

        stream: httpx.Response | None = response
        failure: BaseException | None = None
        attempt = 0
        while True:
            if stream is not None:
                try:
                    await self._consume_sse(stream, cursor, on_message)
                    failure = None
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failure = exc
            if answered or self._closed:
                return
            if failure is not None and not self._is_retryable(failure):
                break
            if cursor.last_event_id is None or attempt >= self._max_retries():
                break
            if cursor.received:
                attempt = 0
            cursor.received = False
            if not await self._sleep_before_retry(self._reconnect_delay(attempt, cursor.retry_ms)):
                return
            attempt += 1
            try:
                stream = await self._open_sse_stream(cursor.last_event_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = exc
                if not self._is_retryable(exc):
                    break
                stream = None
        if self._closed:
            return
        reason = "stream ended without a response" if failure is None else str(failure)
        # Fail just this request. Other in-flight requests keep their own
        # streams, so one broken server does not take down the connection.
        self.emit_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": JSON_RPC_INTERNAL_ERROR,
                    "message": f"MCP response stream failed: {reason}",
                },
            }
        )

    def _start_get_stream(self) -> None:
        if not self.options.open_get_stream or self._get_stream_started or self._closed:
            return
        self._get_stream_started = True
        asyncio.ensure_future(self._run_get_stream())

    async def _run_get_stream(self) -> None:
        """Keep the server-to-client stream open, reconnecting when it drops."""
        cursor = _StreamCursor()
        attempt = 0
        while not self._closed:
            try:
                stream = await self._open_sse_stream(cursor.last_event_id)
                if stream is None:
                    # The server does not offer a GET stream.
                    return
                opened_at = asyncio.get_running_loop().time()
                await self._consume_sse(stream, cursor)
                # A stream that stayed up a while counts as healthy even if it
                # was idle, so a quiet server is not treated as a broken one.
                if cursor.received or (asyncio.get_running_loop().time() - opened_at) > (
                    self._max_delay() / 1000
                ):
                    attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closed:
                    return
                if not self._is_retryable(exc):
                    self.emit_error(exc)
                    return
            cursor.received = False
            if attempt >= self._max_retries():
                self.emit_error(
                    RuntimeError("MCP server-to-client stream dropped and could not be reopened")
                )
                return
            if not await self._sleep_before_retry(self._reconnect_delay(attempt, cursor.retry_ms)):
                return
            attempt += 1

    async def _open_sse_stream(self, last_event_id: str | None) -> httpx.Response | None:
        """Open a GET SSE stream. ``None`` means the server answered 405."""
        headers = {"accept": "text/event-stream"}
        if last_event_id is not None:
            headers["last-event-id"] = last_event_id
        response = await self._authorized_request("GET", headers=headers)
        if response.status_code == 405:
            with contextlib.suppress(Exception):
                await response.aclose()
            return None
        await self._check_response(response)
        self._capture_session(response)
        content_type = _content_type(response)
        if content_type != "text/event-stream":
            with contextlib.suppress(Exception):
                await response.aclose()
            raise McpHttpError(
                response.status_code,
                f"Unsupported MCP GET response content type: {content_type or 'missing'}",
            )
        return response

    def _is_retryable(self, error: BaseException | None) -> bool:
        """Network failures and transient statuses only.

        Auth, session, and protocol errors are not retryable: repeating them
        cannot succeed without a state change the caller has to make.
        """
        if error is None:
            return True
        if isinstance(error, McpHttpError):
            return _is_transient_status(error.status)
        if isinstance(error, (httpx.TransportError, OSError, TimeoutError)):
            return True
        return isinstance(error, (ValueError, ConnectionError))

    def _reconnect_delay(self, attempt: int, server_delay_ms: int | None) -> int:
        if server_delay_ms is not None:
            return server_delay_ms
        return min(DEFAULT_RECONNECT_INITIAL_DELAY_MS * 2**attempt, self._max_delay())

    def _max_delay(self) -> int:
        return (
            self.options.reconnect.max_delay_ms
            if self.options.reconnect
            else DEFAULT_RECONNECT_MAX_DELAY_MS
        )

    def _max_retries(self) -> int:
        return (
            self.options.reconnect.max_retries
            if self.options.reconnect
            else DEFAULT_RECONNECT_MAX_RETRIES
        )

    async def _sleep_before_retry(self, delay_ms: int) -> bool:
        """Sleep, returning False if the transport closed while waiting."""
        if self._closed:
            return False
        self._sleep_task = asyncio.ensure_future(asyncio.sleep(delay_ms / 1000))
        try:
            await self._sleep_task
        except asyncio.CancelledError:
            return False
        finally:
            self._sleep_task = None
        return not self._closed


__all__ = [
    "DEFAULT_RECONNECT_INITIAL_DELAY_MS",
    "DEFAULT_RECONNECT_MAX_DELAY_MS",
    "DEFAULT_RECONNECT_MAX_RETRIES",
    "McpAuthRequiredError",
    "McpHttpError",
    "McpSessionExpiredError",
    "ReconnectOptions",
    "SseEvent",
    "StreamableHttpTransport",
    "StreamableHttpTransportOptions",
]
