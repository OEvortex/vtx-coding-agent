"""OAuth wired through the manager and a real transport.

The unit tests in ``test_oauth.py`` cover the flow itself. What is left is the
seam: that a remote server gets an auth provider, that a 401 lands in
``needs-auth`` rather than an error, that ``sign_in`` stores a token the
transport then actually sends, and that a stdio server is left alone.

Everything runs against one loopback server that is both the MCP server and its
own authorization server -- the single-origin shape that real deployments use.
The MCP half is gated: it answers 401 until a request carries the right bearer
token, and records what it saw. A test that passed against a server ignoring
auth entirely would prove nothing.
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.parse
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from vtx.mcp.config import LoadedMcpConfig, McpServerConfig
from vtx.mcp.manager import McpManager, McpServerConnection
from vtx.mcp.oauth import (
    FileOAuthStateStore,
    McpOAuthProvider,
    McpOAuthState,
    OAuthClientMetadata,
    OAuthTokens,
)
from vtx.mcp.types import LATEST_PROTOCOL_VERSION

pytestmark = pytest.mark.asyncio

ACCESS_TOKEN = "the-access-token"


def _config(url: str, **kwargs: Any) -> McpServerConfig:
    return McpServerConfig(name="remote", url=url, **kwargs)


def _loopback_url(path: str = "/mcp") -> str:
    """A URL that resolves but has nothing listening on it.

    Used for servers that are never actually contacted, so a test that
    accidentally reaches the network fails as a connection error rather than
    quietly succeeding against a real host.
    """
    return f"http://127.0.0.1:9{path}"


# ---- provider wiring ------------------------------------------------------


async def test_a_remote_server_gets_an_auth_provider():
    """Without one the transport has no way to send a token at all."""
    connection = McpServerConnection(_config(_loopback_url()))
    provider = connection._auth_provider()
    assert provider is not None
    assert await provider.token() is None


async def test_a_stdio_server_gets_no_auth_provider():
    """A local process vtx already runs has no authorization server to talk to."""
    connection = McpServerConnection(
        McpServerConfig(name="local", command="npx", args=["-y", "some-server"])
    )
    assert connection._auth_provider() is None


async def test_an_explicit_none_means_no_provider():
    """``None`` has to be distinguishable from "not decided yet"."""
    connection = McpServerConnection(_config(_loopback_url()), auth_provider=None)
    assert connection._auth_provider() is None


async def test_an_injected_provider_is_the_one_used():
    injected = McpOAuthProvider(
        server_url=_loopback_url(),
        redirect_url="",
        client_metadata=OAuthClientMetadata(redirect_uris=[]),
        store=_MemoryStore(),
    )
    await injected.save_tokens(OAuthTokens(access_token="stored", token_type="Bearer"))
    connection = McpServerConnection(
        _config(_loopback_url()), oauth_provider_factory=lambda c, redirect_url="": injected
    )
    assert await connection._auth_provider().token() == "stored"


# ---- the gated server -----------------------------------------------------


@dataclass
class Gate:
    """What the gate saw, and whether it is open."""

    expected: str | None = ACCESS_TOKEN
    seen: list[str | None] | None = None
    registrations: int = 0
    refuse_registration: bool = False
    # Where the 401 says the protected-resource metadata lives. Pointing this
    # at a dead port is how "the server wants auth and we cannot find out how"
    # is reproduced.
    metadata_url: str | None = None

    def __post_init__(self) -> None:
        if self.seen is None:
            self.seen = []

    def admits(self, header: str | None) -> bool:
        token = header[7:] if header and header.startswith("Bearer ") else None
        self.seen.append(token)
        return self.expected is None or token == self.expected


@dataclass
class CombinedServer:
    """An MCP server and its own authorization server on one origin."""

    gate: Gate
    server: Any = None
    origin: str = ""

    @property
    def mcp_url(self) -> str:
        return f"{self.origin}/mcp"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _combined_server(gate: Gate | None = None) -> CombinedServer:
    running = CombinedServer(gate=gate or Gate())
    state = running

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:
            pass

        @property
        def _origin(self) -> str:
            return self.server.origin  # type: ignore[attr-defined]

        def _json(self, status: int, payload: Any, extra: dict[str, str] | None = None) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> str:
            length = int(self.headers.get("content-length") or 0)
            return self.rfile.read(length).decode() if length else ""

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/.well-known/oauth-protected-resource/mcp":
                self._json(
                    200,
                    {
                        "resource": f"{self._origin}/mcp",
                        "authorization_servers": [self._origin],
                        "scopes_supported": ["mcp:read"],
                    },
                )
                return
            if path == "/.well-known/oauth-authorization-server":
                self._json(
                    200,
                    {
                        "issuer": self._origin,
                        "authorization_endpoint": f"{self._origin}/authorize",
                        "token_endpoint": f"{self._origin}/token",
                        "registration_endpoint": f"{self._origin}/register",
                        "response_types_supported": ["code"],
                        "grant_types_supported": ["authorization_code", "refresh_token"],
                        "token_endpoint_auth_methods_supported": ["none"],
                        "code_challenge_methods_supported": ["S256"],
                    },
                )
                return
            if path == "/mcp":
                # The transport opens a GET stream after initialize. It is
                # optional, so declining it is correct -- but 404 reads as a
                # failed request, where 405 reads as "not offered".
                self.send_response(405)
                self.send_header("content-length", "0")
                self.end_headers()
                return
            if path == "/authorize":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                location = (
                    f"{query['redirect_uri'][0]}"
                    f"?code=the-code&state={query['state'][0]}&iss={self._origin}"
                )
                self.send_response(302)
                self.send_header("location", location)
                self.send_header("content-length", "0")
                self.end_headers()
                return
            self._json(404, {"error": "not_found"})

        def do_DELETE(self) -> None:
            self.send_response(200)
            self.send_header("content-length", "0")
            self.end_headers()

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            # Drain the body on every POST, including the ones that ignore it.
            # With keep-alive an unread body is parsed as the start of the next
            # request, which fails in a way that looks nothing like its cause.
            body = self._read_body()

            if path == "/register":
                state.gate.registrations += 1
                if state.gate.refuse_registration:
                    self._json(400, {"error": "invalid_redirect_uri"})
                    return
                self._json(201, {"client_id": "cid"})
                return
            if path == "/token":
                self._json(
                    200,
                    {
                        "access_token": ACCESS_TOKEN,
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "refresh_token": "the-refresh-token",
                    },
                )
                return
            if path != "/mcp":
                self._json(404, {"error": "not_found"})
                return

            # Parsed from the body already drained above, not read again: the
            # bytes are gone from the socket, so a second read would block
            # waiting for a request that is not coming.
            message = _parse_json(body)

            if not state.gate.admits(self.headers.get("authorization")):
                self._json(
                    401,
                    {"error": "invalid_token"},
                    extra={
                        "www-authenticate": (
                            f'Bearer error="invalid_token", '
                            f'resource_metadata="{
                                state.gate.metadata_url
                                or self._origin + "/.well-known/oauth-protected-resource/mcp"
                            }"'
                        )
                    },
                )
                return

            if message is None or "id" not in message:
                # A notification expects the bare 202 acknowledgement, not a
                # JSON-RPC envelope with nothing in it.
                self.send_response(202)
                self.send_header("content-length", "0")
                self.end_headers()
                return
            self._json(200, _rpc_result(message))

    class _Server(ThreadingHTTPServer):
        daemon_threads = True
        origin = ""

    server = _Server(("127.0.0.1", 0), Handler)
    host, port = server.server_address[:2]
    server.origin = f"http://{host}:{port}"
    running.server = server
    running.origin = server.origin
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return running


def _parse_json(body: str) -> dict[str, Any] | None:
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _rpc_result(message: dict[str, Any] | None) -> dict[str, Any]:
    if message is None or "id" not in message:
        return {}
    method = message.get("method")
    if method == "initialize":
        result: Any = {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "gated", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "hello"}]}
    else:
        result = {}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


# ---- the 401 path ---------------------------------------------------------


async def test_a_401_leaves_the_server_in_needs_auth():
    server = _combined_server()
    try:
        connection = McpServerConnection(_config(server.mcp_url))
        await connection.connect()
        assert connection.status.state == "needs-auth"
        assert connection.definitions == []
    finally:
        server.stop()


async def test_the_401_reason_survives_for_the_report():
    """Discovery is reachable here, so the only blocker really is the token."""
    server = _combined_server()
    try:
        connection = McpServerConnection(_config(server.mcp_url))
        await connection.connect()
        assert "needs sign-in" in connection.status.describe()
    finally:
        server.stop()


async def test_a_server_that_ignores_auth_still_connects():
    """A permissive server must not make a broken auth setup look like success."""
    server = _combined_server(Gate(expected=None))
    try:
        connection = McpServerConnection(_config(server.mcp_url))
        await connection.connect()
        assert connection.status.state == "connected"
        assert [d["name"] for d in connection.definitions] == ["echo"]
        assert [t.name for t in connection.build_tools(lambda _n: False)] == ["mcp__remote__echo"]
    finally:
        server.stop()


async def test_an_undiscoverable_auth_server_still_says_needs_auth():
    """A 401 whose auth server cannot be reached is still a sign-in problem.

    Reporting it as a plain failure would send the user looking at connectivity
    instead of at their credentials. The MCP server itself is perfectly
    reachable here -- only the authorization metadata is not.
    """
    server = _combined_server(
        Gate(metadata_url="http://127.0.0.1:9/.well-known/oauth-protected-resource")
    )
    try:
        connection = McpServerConnection(_config(server.mcp_url))
        await connection.connect()
        assert connection.status.state == "needs-auth"
        assert "could not authenticate" in (connection.status.error or "")
        assert "needs sign-in" in connection.status.describe()
    finally:
        server.stop()


# ---- sign in --------------------------------------------------------------


def _manager(url: str, store: Any, tmp_path) -> McpManager:
    """A manager over one remote server, wired to a store the test owns.

    ``redirect_url`` is empty because ``redirect_uris`` records whatever the
    flow's own loopback listener is bound to; the provider is rebuilt per sign-in
    for exactly that reason.
    """

    def factory(config: McpServerConfig, redirect_url: str = "") -> McpOAuthProvider:
        # redirect_uris records whatever loopback listener the flow just bound,
        # which is why the provider is built per sign-in rather than once.
        return McpOAuthProvider(
            server_url=config.url or "",
            redirect_url=redirect_url,
            client_metadata=OAuthClientMetadata(
                redirect_uris=[redirect_url] if redirect_url else [], client_name="vtx-test"
            ),
            store=store,
            on_redirect=_browse,
        )

    return McpManager(
        cwd=str(tmp_path),
        config=LoadedMcpConfig(servers=[_config(url)]),
        oauth_provider_factory=factory,
    )


async def test_signing_in_stores_a_token_the_transport_then_sends(tmp_path):
    """The whole loop, against a server that actually checks the token."""
    server = _combined_server()
    store = FileOAuthStateStore(tmp_path / "mcp-auth.json")
    manager = _manager(server.mcp_url, store, tmp_path)
    try:
        connection = manager.get("remote")
        assert connection is not None

        await connection.connect()
        assert connection.status.state == "needs-auth"
        assert all(token is None for token in server.gate.seen)

        assert await manager.sign_in("remote", open_url=_browse) is True

        # The transport builds its provider from the same store, so the token
        # just saved is the one now on the wire -- no reload dance needed.
        await connection.reconnect()
        assert connection.status.state == "connected"
        assert [d["name"] for d in connection.definitions] == ["echo"]
        assert ACCESS_TOKEN in server.gate.seen

        # And the tool works, which is the entire point of all of this.
        tools = [t for t in manager.rebuild_tools() if t.name == "mcp__remote__echo"]
        assert [t.name for t in tools] == ["mcp__remote__echo"]
        result = await tools[0].execute({})
        assert "hello" in str(getattr(result, "output", result))
    finally:
        await manager.close()
        server.stop()


async def test_signing_in_twice_reuses_the_registration(tmp_path):
    """A repeated sign-in must not pile up new clients at the server."""
    server = _combined_server()
    store = FileOAuthStateStore(tmp_path / "mcp-auth.json")
    manager = _manager(server.mcp_url, store, tmp_path)
    try:
        assert await manager.sign_in("remote", open_url=_browse) is True
        assert server.gate.registrations == 1
        # The second run has a usable refresh token, so it needs no browser.
        assert await manager.sign_in("remote", open_url=_browse) is True
        assert server.gate.registrations == 1
    finally:
        await manager.close()
        server.stop()


async def test_a_refused_registration_is_reported_not_hidden(tmp_path):
    server = _combined_server(Gate(refuse_registration=True))
    store = FileOAuthStateStore(tmp_path / "mcp-auth.json")
    manager = _manager(server.mcp_url, store, tmp_path)
    try:
        assert await manager.sign_in("remote", open_url=_browse) is False
        status = manager.get("remote").status
        assert status.state == "needs-auth"
        assert "registration" in (status.error or "").lower()
        assert "needs sign-in" in status.describe()
    finally:
        await manager.close()
        server.stop()


async def test_signing_in_a_stdio_server_is_refused():
    connection = McpServerConnection(McpServerConfig(name="local", command="npx"))
    assert await connection.sign_in() is False
    assert connection.status.state == "needs-auth"


async def test_signing_in_an_unknown_server_is_refused(tmp_path):
    manager = _manager(_loopback_url(), _MemoryStore(), tmp_path)
    try:
        assert await manager.sign_in("nope") is False
    finally:
        await manager.close()


# ---- sign out -------------------------------------------------------------


async def test_signing_out_clears_the_stored_token(tmp_path):
    """A bad credential has to be clearable, or the server is stuck forever."""
    store = FileOAuthStateStore(tmp_path / "mcp-auth.json")
    provider = McpOAuthProvider(
        server_url=_loopback_url(),
        redirect_url="",
        client_metadata=OAuthClientMetadata(redirect_uris=[]),
        store=store,
    )
    await provider.save_tokens(OAuthTokens(access_token="stale", token_type="Bearer"))

    # A separately-built provider over the same file, so this is a real round
    # trip through disk rather than one object answering twice.
    fresh = McpOAuthProvider(
        server_url=_loopback_url(),
        redirect_url="",
        client_metadata=OAuthClientMetadata(redirect_uris=[]),
        store=FileOAuthStateStore(tmp_path / "mcp-auth.json"),
    )
    tokens = await fresh.tokens()
    assert tokens is not None and tokens.access_token == "stale"

    await fresh.invalidate_credentials("all")
    assert await fresh.tokens() is None
    assert await provider.tokens() is None


# ---- helpers --------------------------------------------------------------


class _MemoryStore:
    """An in-process store, so these tests never touch a real credentials file."""

    def __init__(self) -> None:
        self._value: McpOAuthState | None = None

    async def load(self) -> McpOAuthState | None:
        return McpOAuthState(**asdict(self._value)) if self._value else None

    async def save(self, state: McpOAuthState) -> None:
        self._value = McpOAuthState(**asdict(state))


async def _browse(url: str) -> None:
    """Play the browser: fetch the authorize URL and follow its redirect."""

    def _get() -> None:
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            client.get(url)

    await asyncio.to_thread(_get)
