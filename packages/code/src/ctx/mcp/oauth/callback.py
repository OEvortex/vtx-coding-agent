"""Loopback HTTP server that receives the OAuth redirect.

RFC 8252 section 7.3: a native client that cannot keep a stable public redirect
URI listens on loopback and uses whatever port it got. This is that listener,
built on ``asyncio.start_server`` rather than ``http.server`` so it lives in the
same event loop as the rest of the flow and needs no thread.

The browser page is deliberately plain: the useful content is one line saying
whether the sign-in worked, and the window is about to be closed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("mcp.oauth.callback")

DEFAULT_TIMEOUT_SECONDS = 5 * 60


@dataclass
class OAuthCallback:
    code: str
    state: str
    issuer: str | None = None


@dataclass
class CallbackPage:
    """What the browser is told. Rendered as text, or as HTML if asked."""

    ok: bool
    message: str = ""
    details: str = ""


def _plain_text(page: CallbackPage) -> str:
    if page.ok:
        return "Authorization complete. You may close this window."
    return f"{page.message}\n\n{page.details}" if page.details else page.message


@dataclass
class PendingCallback:
    future: asyncio.Future
    timer: asyncio.TimerHandle | None = None


@dataclass
class OAuthCallbackServer:
    """A one-shot loopback listener for the authorization redirect.

    Not reusable: :meth:`close` shuts the socket, and a new sign-in makes a new
    server. That keeps port and state lifetime the same, which is the whole
    point of a loopback redirect.
    """

    redirect_url: str
    _path: str
    _timeout: float
    _render_page: Callable[[CallbackPage], str] | None = None
    _server: Any = None
    _pending: dict[str, PendingCallback] = field(default_factory=dict)
    _closed: bool = False

    @classmethod
    async def listen(
        cls,
        *,
        host: str = "127.0.0.1",
        redirect_host: str | None = None,
        port: int = 0,
        path: str = "/callback",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        render_page: Callable[[CallbackPage], str] | None = None,
    ) -> OAuthCallbackServer:
        """Bind a loopback socket and return a server waiting for the redirect.

        ``redirect_host`` is the host name to put in the redirect URI, which may
        differ from ``host``: a client registered against
        ``http://localhost:PORT/callback`` has to be redirected there even
        though we bind ``127.0.0.1``.
        """
        instance = cls(
            redirect_url="", _path=path, _timeout=timeout_seconds, _render_page=render_page
        )
        instance._server = await asyncio.start_server(
            instance._handle_client, host, port, reuse_address=True
        )
        bound = instance._server.sockets[0].getsockname()
        actual_port = bound[1]
        advertised = redirect_host or host
        # Bracket an IPv6 literal; a bare one is not a valid URI authority.
        authority = (
            f"[{advertised}]"
            if ":" in advertised and not advertised.startswith("[")
            else advertised
        )
        instance.redirect_url = f"http://{authority}:{actual_port}{path}"
        return instance

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1] if self._server else 0

    async def wait_for_callback(self, state: str) -> OAuthCallback:
        """Resolve when the browser redirects back with a matching ``state``."""
        if state in self._pending:
            raise RuntimeError("OAuth state is already pending")
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        entry = PendingCallback(future=future)
        entry.timer = loop.call_later(self._timeout, self._expire, state)
        self._pending[state] = entry
        return await future

    def _expire(self, state: str) -> None:
        entry = self._pending.pop(state, None)
        if entry is not None and not entry.future.done():
            entry.future.set_exception(TimeoutError("OAuth callback timed out"))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for entry in list(self._pending.values()):
            if entry.timer is not None:
                entry.timer.cancel()
            if not entry.future.done():
                entry.future.set_exception(RuntimeError("OAuth callback server closed"))
        self._pending.clear()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

    # ---- wire ------------------------------------------------------------

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not request_line:
                return
            # Headers are drained but unused: the redirect is a bare GET.
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                if line in (b"\r\n", b"\n", b""):
                    break

            parts = request_line.decode("latin-1").split()
            target = parts[1] if len(parts) > 1 else "/"
            page, status = self._route(target)
            writer.write(self._response(status, page))
            await writer.drain()
        except (TimeoutError, ConnectionError):
            pass
        except Exception:
            log.debug("MCP OAuth callback request failed", exc_info=True)
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    def _route(self, target: str) -> tuple[CallbackPage, int]:
        url = urlparse(target)
        if url.path != self._path:
            return CallbackPage(ok=False, message="Not found"), 404

        query = parse_qs(url.query)
        state = (query.get("state") or [""])[0]
        entry = self._pending.get(state) if state else None
        if not state or entry is None:
            # A callback we did not ask for. Saying "invalid or expired" rather
            # than the specific reason keeps a probe from learning which
            # state values are live.
            return CallbackPage(ok=False, message="Invalid or expired OAuth state"), 400

        if entry.timer is not None:
            entry.timer.cancel()
        self._pending.pop(state, None)

        error = (query.get("error") or [""])[0]
        if error:
            details = (query.get("error_description") or [error])[0]
            if not entry.future.done():
                entry.future.set_exception(PermissionError(details))
            return (
                CallbackPage(
                    ok=False,
                    message="Authorization failed. You may close this window.",
                    details=details,
                ),
                200,
            )

        code = (query.get("code") or [""])[0]
        if not code:
            if not entry.future.done():
                entry.future.set_exception(
                    ValueError("OAuth callback did not include an authorization code")
                )
            return CallbackPage(ok=False, message="Missing authorization code"), 400

        issuer = (query.get("iss") or [None])[0]
        if not entry.future.done():
            entry.future.set_result(OAuthCallback(code=code, state=state, issuer=issuer))
        return CallbackPage(ok=True), 200

    def _response(self, status: int, page: CallbackPage) -> bytes:
        if self._render_page is not None:
            body = self._render_page(page)
            content_type = "text/html; charset=utf-8"
        else:
            body = _plain_text(page)
            content_type = "text/plain; charset=utf-8"
        payload = body.encode("utf-8")
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}.get(status, "OK")
        return (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("latin-1") + payload


__all__ = ["CallbackPage", "OAuthCallback", "OAuthCallbackServer"]
