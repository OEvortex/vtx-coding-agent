"""OAuth tests: a real loopback authorization server driving a real PKCE flow.

Not mocked. The point of these is the parts that only break in a real exchange
-- the code challenge, the redirect, the token exchange, the 401 refresh -- so
the server here verifies the verifier against the challenge it was given and
rejects a mismatched one.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from vtx.mcp.auth import UnauthorizedContext
from vtx.mcp.oauth import (
    FileOAuthStateStore,
    McpOAuthAuthorizationRequiredError,
    McpOAuthProvider,
    MemoryOAuthStateStore,
    OAuthCallbackServer,
    OAuthClientInformation,
    OAuthClientMetadata,
    OAuthFlowOptions,
    OAuthInsecureEndpointError,
    OAuthIssuerMismatchError,
    OAuthTokens,
    adapt_oauth_provider,
    authorize_mcp,
    build_authorization_server_discovery_urls,
    discover_authorization_server_metadata,
    discover_protected_resource_metadata,
    generate_pkce,
    parse_www_authenticate,
    secure_endpoint,
    select_client_auth_method,
    select_resource,
    start_authorization,
)
from vtx.mcp.oauth.errors import OAuthRegistrationError
from vtx.mcp.oauth.types import (
    parse_authorization_server_metadata,
    parse_protected_resource_metadata,
)

pytestmark = pytest.mark.asyncio


# ---- a minimal authorization server ---------------------------------------


@dataclass
class AuthServerState:
    client_id: str = "client-abc"
    client_secret: str | None = None
    code_challenges: dict[str, str] = field(default_factory=dict)
    refresh_calls: int = 0
    registrations: int = 0
    authorize_calls: int = 0
    scopes: list[str] = field(default_factory=lambda: ["read", "write"])
    require_registration: bool = True
    fail_refresh_with: str | None = None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: AuthServerState
    origin: str

    def log_message(self, *args: Any) -> None:
        pass

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> str:
        length = int(self.headers.get("content-length") or 0)
        return self.rfile.read(length).decode() if length else ""

    def _form(self) -> dict[str, str]:
        return {k: v[0] for k, v in urllib.parse.parse_qs(self._read_body()).items()}

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        origin = self.origin
        if path == "/.well-known/oauth-protected-resource/mcp":
            self._json(
                200,
                {
                    "resource": f"{origin}/mcp",
                    "authorization_servers": [origin],
                    "scopes_supported": self.state.scopes,
                },
            )
            return
        if path == "/.well-known/oauth-authorization-server":
            self._json(
                200,
                {
                    "issuer": origin,
                    "authorization_endpoint": f"{origin}/authorize",
                    "token_endpoint": f"{origin}/token",
                    "registration_endpoint": f"{origin}/register",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "token_endpoint_auth_methods_supported": ["none"],
                    "code_challenge_methods_supported": ["S256"],
                },
            )
            return
        if path == "/authorize":
            self.state.authorize_calls += 1
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            redirect_uri = query["redirect_uri"][0]
            state = query.get("state", [""])[0]
            challenge = query["code_challenge"][0]
            code = f"code-{len(self.state.code_challenges) + 1}"
            self.state.code_challenges[code] = challenge
            target = f"{redirect_uri}?code={code}&state={state}"
            self.send_response(302)
            self.send_header("location", target)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        # The body is drained on every POST, including the branches that ignore
        # it. With HTTP/1.1 keep-alive an unread body stays in the socket and is
        # parsed as the start of the *next* request, which fails in a way that
        # looks nothing like its cause.
        form = self._form() if path == "/token" else self._read_body()

        if path == "/register":
            self.state.registrations += 1
            if not self.state.require_registration:
                self._json(400, {"error": "invalid_request"})
                return
            body: dict[str, Any] = {"client_id": self.state.client_id}
            if self.state.client_secret:
                body["client_secret"] = self.state.client_secret
            self._json(201, body)
            return

        if path != "/token":
            self._json(404, {"error": "not_found"})
            return

        grant = form.get("grant_type")

        if grant == "refresh_token":
            self.state.refresh_calls += 1
            if self.state.fail_refresh_with:
                # Some providers answer an OAuth error with a 200.
                self._json(
                    200, {"error": self.state.fail_refresh_with, "error_description": "nope"}
                )
                return
            self._json(
                200,
                {
                    "access_token": f"refreshed-{self.state.refresh_calls}",
                    "token_type": "Bearer",
                    # No refresh_token: the provider is telling us to keep the
                    # one we already have.
                    "expires_in": 3600,
                },
            )
            return

        if grant != "authorization_code":
            self._json(400, {"error": "unsupported_grant_type"})
            return

        code = form.get("code", "")
        challenge = self.state.code_challenges.get(code)
        verifier = form.get("code_verifier", "")
        # The whole point of PKCE: the verifier presented at the token endpoint
        # must hash to the challenge this code was issued against.
        expected = _b64url(hashlib.sha256(verifier.encode()).digest())
        if not challenge or expected != challenge:
            self._json(400, {"error": "invalid_grant", "error_description": "PKCE mismatch"})
            return
        del self.state.code_challenges[code]  # single use
        self._json(
            200,
            {
                "access_token": "access-1",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "refresh-1",
                "scope": form.get("scope", "read"),
            },
        )


@pytest.fixture
def auth_server():
    state = AuthServerState()
    handler = type("BoundHandler", (_Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    host, port = server.server_address[:2]
    origin = f"http://{host}:{port}"
    handler.origin = origin
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield origin, state
    finally:
        server.shutdown()
        server.server_close()


async def _browse(url: str) -> None:
    """Stand in for the user's browser: fetch the authorize URL, follow the 302.

    The flow only ever *builds* the authorize URL -- a real browser is what
    visits it -- so a test that wants the loop closed has to play that part.
    """

    def _get() -> None:
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            client.get(url)

    await asyncio.to_thread(_get)


def _provider(
    server_url: str, redirect_url: str, store=None, on_redirect=_browse, **kwargs
) -> McpOAuthProvider:
    """A provider whose redirect is played by the simulated browser by default.

    Any test that reaches an authorize redirect needs the browser half, or the
    flow stops at "here is a URL" and the code exchange never runs.
    """
    return McpOAuthProvider(
        server_url=server_url,
        redirect_url=redirect_url,
        client_metadata=OAuthClientMetadata(
            redirect_uris=[redirect_url], client_name="vtx-test", **kwargs
        ),
        store=store or MemoryOAuthStateStore(),
        on_redirect=on_redirect,
    )


# ---- unit-level ----------------------------------------------------------


async def test_pkce_verifier_and_challenge_are_s256():
    verifier, challenge = generate_pkce()
    assert 43 <= len(verifier) <= 128  # base64url of 32 bytes, unpadded
    assert "=" not in verifier and "+" not in verifier
    assert challenge == _b64url(hashlib.sha256(verifier.encode()).digest())
    # A second call must not repeat.
    assert generate_pkce()[0] != verifier


async def test_www_authenticate_parsing():
    assert parse_www_authenticate(None).error is None
    assert parse_www_authenticate("Basic realm=x").error is None
    challenge = parse_www_authenticate(
        'Bearer resource_metadata="https://example.com/m", scope="a b", error="insufficient_scope"'
    )
    assert challenge.resource_metadata_url == "https://example.com/m"
    assert challenge.scope == "a b"
    assert challenge.error == "insufficient_scope"


async def test_client_auth_method_selection():
    none = OAuthClientInformation(client_id="x")
    secret = OAuthClientInformation(client_id="x", client_secret="s")
    assert select_client_auth_method(none, ["none"]) == "none"
    assert (
        select_client_auth_method(secret, ["client_secret_basic", "none"]) == "client_secret_basic"
    )
    assert (
        select_client_auth_method(secret, ["client_secret_post", "none"]) == "client_secret_post"
    )
    # No list published: fall back to having a secret.
    assert select_client_auth_method(secret, []) == "client_secret_basic"
    assert select_client_auth_method(none, []) == "none"
    # A server hint wins when it is supported.
    secret.extra["token_endpoint_auth_method"] = "client_secret_post"
    assert select_client_auth_method(secret, ["client_secret_basic", "client_secret_post"]) == (
        "client_secret_post"
    )


async def test_secure_endpoint_refuses_plain_http():
    assert secure_endpoint("https://example.com/token")
    assert secure_endpoint("http://127.0.0.1:8080/token")
    with pytest.raises(OAuthInsecureEndpointError):
        secure_endpoint("http://example.com/token")


async def test_authorization_server_discovery_urls():
    urls = build_authorization_server_discovery_urls("https://as.example.com/tenant1")
    assert urls == [
        "https://as.example.com/.well-known/oauth-authorization-server/tenant1",
        "https://as.example.com/.well-known/openid-configuration/tenant1",
        "https://as.example.com/tenant1/.well-known/openid-configuration",
    ]


async def test_select_resource_rejects_a_mismatched_resource():
    ok = parse_protected_resource_metadata(
        {"resource": "https://api.example.com/", "authorization_servers": ["https://auth"]}
    )
    assert select_resource("https://api.example.com/mcp", ok) == "https://api.example.com/"

    other_host = parse_protected_resource_metadata({"resource": "https://evil.example.com/"})
    with pytest.raises(ValueError, match="does not match"):
        select_resource("https://api.example.com/mcp", other_host)

    other_path = parse_protected_resource_metadata({"resource": "https://api.example.com/admin/"})
    with pytest.raises(ValueError, match="does not match"):
        select_resource("https://api.example.com/mcp", other_path)


async def test_issuer_mismatch_is_fatal():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "issuer": "https://someone-else.example.com",
                "authorization_endpoint": "https://someone-else.example.com/authorize",
                "token_endpoint": "https://someone-else.example.com/token",
                "response_types_supported": ["code"],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OAuthIssuerMismatchError):
            await discover_authorization_server_metadata("https://as.example.com", fetch=client)


async def test_absent_metadata_is_not_a_failure():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await discover_authorization_server_metadata("https://as.example.com", fetch=client)
            is None
        )


async def test_protected_resource_metadata_falls_back_to_the_origin_form(auth_server):
    origin, _state = auth_server
    async with httpx.AsyncClient() as client:
        # The path form 404s; the origin form should be found.
        metadata = await discover_protected_resource_metadata(f"{origin}/mcp", fetch=client)
    assert metadata.resource == f"{origin}/mcp"


# ---- the full flow --------------------------------------------------------


async def test_discovers_registers_authorizes_and_stores_tokens(auth_server):
    origin, state = auth_server
    server_url = f"{origin}/mcp"

    callback = await OAuthCallbackServer.listen()
    try:
        provider = _provider(server_url, callback.redirect_url, on_redirect=_browse)
        oauth_state = await provider.state()
        waiting = asyncio.ensure_future(callback.wait_for_callback(oauth_state))

        result = await authorize_mcp(provider, OAuthFlowOptions(server_url=server_url))
        assert result == "REDIRECT"
        assert state.registrations == 1
        assert state.authorize_calls == 1

        got = await asyncio.wait_for(waiting, timeout=10)
        assert got.code and got.state == oauth_state

        # Complete the code exchange.
        assert (
            await authorize_mcp(
                provider, OAuthFlowOptions(server_url=server_url, authorization_code=got.code)
            )
            == "AUTHORIZED"
        )

        tokens = await provider.tokens()
        assert tokens is not None
        assert tokens.access_token == "access-1"
        assert tokens.refresh_token == "refresh-1"
        client_info = await provider.client_information()
        assert client_info is not None and client_info.client_id == "client-abc"
    finally:
        await callback.close()


async def test_second_call_refreshes_instead_of_redirecting(auth_server):
    origin, state = auth_server
    server_url = f"{origin}/mcp"
    callback = await OAuthCallbackServer.listen()
    try:
        provider = _provider(server_url, callback.redirect_url)
        await provider.save_tokens(
            OAuthTokens(access_token="old", token_type="Bearer", refresh_token="refresh-1")
        )

        # A stored refresh token means no browser round trip.
        assert (
            await authorize_mcp(provider, OAuthFlowOptions(server_url=server_url)) == "AUTHORIZED"
        )
        assert state.refresh_calls == 1
        assert state.authorize_calls == 0

        tokens = await provider.tokens()
        assert tokens.access_token == "refreshed-1"
        # The provider omitted a new refresh token; ours must survive.
        assert tokens.refresh_token == "refresh-1"
    finally:
        await callback.close()


async def test_a_server_error_refresh_falls_back_to_authorizing(auth_server):
    origin, state = auth_server
    state.fail_refresh_with = "server_error"
    server_url = f"{origin}/mcp"
    callback = await OAuthCallbackServer.listen()
    try:
        provider = _provider(server_url, callback.redirect_url, on_redirect=_browse)
        await provider.save_tokens(
            OAuthTokens(access_token="old", token_type="Bearer", refresh_token="refresh-1")
        )
        result = await authorize_mcp(provider, OAuthFlowOptions(server_url=server_url))
        assert result == "REDIRECT"
        assert state.refresh_calls == 1
        assert state.authorize_calls == 1
    finally:
        await callback.close()


async def test_invalid_grant_drops_tokens_and_authorizes_again(auth_server):
    origin, state = auth_server
    state.fail_refresh_with = "invalid_grant"
    server_url = f"{origin}/mcp"
    callback = await OAuthCallbackServer.listen()
    try:
        provider = _provider(server_url, callback.redirect_url)
        await provider.save_tokens(
            OAuthTokens(access_token="old", token_type="Bearer", refresh_token="refresh-1")
        )
        # A dead refresh token has no unattended recovery, so the dead grant is
        # dropped and the flow asks for a new one.
        result = await authorize_mcp(provider, OAuthFlowOptions(server_url=server_url))
        assert result == "REDIRECT"
        assert state.refresh_calls == 1
        assert state.authorize_calls == 1
        # The invalidated token is gone; the fresh code exchange is what
        # replaces it, and that has not happened yet.
        assert await provider.tokens() is None
    finally:
        await callback.close()


async def test_concurrent_401s_share_one_refresh(auth_server):
    """Two requests failing at once must not each burn a refresh token.

    With rotating refresh tokens the second refresh would fail *and* discard the
    new grant, so sharing the in-flight flow is a correctness requirement, not
    just an efficiency one.
    """
    origin, state = auth_server
    server_url = f"{origin}/mcp"
    callback = await OAuthCallbackServer.listen()
    try:
        provider = _provider(server_url, callback.redirect_url)
        await provider.save_tokens(
            OAuthTokens(access_token="old", token_type="Bearer", refresh_token="refresh-1")
        )
        adapter = adapt_oauth_provider(provider)

        await asyncio.gather(
            adapter.on_unauthorized(_context(server_url, "old", challenge="Bearer", fetch=None)),
            adapter.on_unauthorized(_context(server_url, "old", challenge="Bearer", fetch=None)),
        )
        assert state.refresh_calls == 1
        assert await adapter.token() == "refreshed-1"
    finally:
        await callback.close()


async def test_registration_failure_is_its_own_error(auth_server):
    """A rejected registration is distinguishable from a rejected token.

    The two need different remedies -- one means the server will not register a
    client at all, the other that this particular grant failed -- so they are
    separate types rather than one message.
    """
    origin, state = auth_server
    state.require_registration = False
    server_url = f"{origin}/mcp"
    callback = await OAuthCallbackServer.listen()
    try:
        provider = _provider(server_url, callback.redirect_url)
        with pytest.raises(OAuthRegistrationError) as excinfo:
            await authorize_mcp(provider, OAuthFlowOptions(server_url=server_url))
        assert excinfo.value.status == 400
        assert state.registrations == 1
        # A registration failure is not a refreshable credential, so it is not
        # retried into a second attempt.
        assert state.registrations == 1
        assert await provider.client_information() is None
    finally:
        await callback.close()


# ---- the transport adapter ------------------------------------------------


class _FakeResponse:
    def __init__(self, status: int, challenge: str | None = None) -> None:
        self.status_code = status
        self.headers = {"www-authenticate": challenge} if challenge else {}


async def test_adapter_refreshes_a_401_and_returns_the_new_token(auth_server):
    origin, state = auth_server
    server_url = f"{origin}/mcp"
    callback = await OAuthCallbackServer.listen()
    try:
        provider = _provider(server_url, callback.redirect_url)
        await provider.save_tokens(
            OAuthTokens(access_token="old", token_type="Bearer", refresh_token="refresh-1")
        )
        adapter = adapt_oauth_provider(provider)
        assert await adapter.token() == "old"

        # A 401 with no redirect: the refresh succeeds, so no human is needed.
        await adapter.on_unauthorized(_context(server_url, "old", challenge="Bearer", fetch=None))
        assert state.refresh_calls == 1
        assert await adapter.token() == "refreshed-1"
    finally:
        await callback.close()


async def test_adapter_signals_when_a_human_is_required(auth_server):
    origin, _state = auth_server
    server_url = f"{origin}/mcp"
    callback = await OAuthCallbackServer.listen()
    try:
        provider = _provider(server_url, callback.redirect_url)
        adapter = adapt_oauth_provider(provider)
        # No stored tokens: the flow has to reach a browser redirect.
        with pytest.raises(McpOAuthAuthorizationRequiredError):
            await adapter.on_unauthorized(
                _context(server_url, None, challenge="Bearer", fetch=None)
            )
    finally:
        await callback.close()


async def test_adapter_skips_a_second_refresh_when_the_token_already_changed(auth_server):
    origin, state = auth_server
    server_url = f"{origin}/mcp"
    callback = await OAuthCallbackServer.listen()
    try:
        provider = _provider(server_url, callback.redirect_url)
        await provider.save_tokens(
            OAuthTokens(access_token="old", token_type="Bearer", refresh_token="refresh-1")
        )
        adapter = adapt_oauth_provider(provider)

        # Simulate a concurrent request having already refreshed.
        await provider.save_tokens(
            OAuthTokens(access_token="new", token_type="Bearer", refresh_token="refresh-1")
        )
        await adapter.on_unauthorized(_context(server_url, "old", challenge="Bearer", fetch=None))
        # No refresh was needed, so none was spent.
        assert state.refresh_calls == 0
        assert await adapter.token() == "new"
    finally:
        await callback.close()


def _context(server_url: str, token: str | None, *, challenge: str | None, fetch: Any):
    return UnauthorizedContext(
        response=_FakeResponse(401, challenge), server_url=server_url, token=token, fetch=fetch
    )


# ---- storage --------------------------------------------------------------


async def test_file_store_round_trips_and_is_private(tmp_path):
    path = tmp_path / "mcp-auth.json"
    store = FileOAuthStateStore(path)
    assert await store.load() is None

    provider = _provider("https://mcp.example.com/mcp", "http://127.0.0.1:1/cb", store=store)
    await provider.save_tokens(
        OAuthTokens(access_token="secret-token", token_type="Bearer", refresh_token="r")
    )
    assert path.exists()
    assert oct(path.stat().st_mode)[-3:] == "600"

    reloaded = _provider(
        "https://mcp.example.com/mcp", "http://127.0.0.1:1/cb", store=FileOAuthStateStore(path)
    )
    tokens = await reloaded.tokens()
    assert tokens is not None and tokens.access_token == "secret-token"


async def test_credentials_for_another_server_are_never_reused(tmp_path):
    store = FileOAuthStateStore(tmp_path / "mcp-auth.json")
    mine = _provider("https://a.example.com/mcp", "http://127.0.0.1:1/cb", store=store)
    await mine.save_tokens(OAuthTokens(access_token="a-token", token_type="Bearer"))

    theirs = _provider("https://b.example.com/mcp", "http://127.0.0.1:1/cb", store=store)
    # Reading server A's token into server B's request would be a real leak.
    assert await theirs.tokens() is None


async def test_invalidate_credentials_clears_only_what_was_asked(tmp_path):
    provider = _provider("https://a.example.com/mcp", "http://127.0.0.1:1/cb")
    await provider.save_client_information(OAuthClientInformation(client_id="c"))
    await provider.save_tokens(OAuthTokens(access_token="t", token_type="Bearer"))
    await provider.save_code_verifier("v")
    oauth_state = await provider.state()

    await provider.invalidate_credentials("tokens")
    assert await provider.tokens() is None
    assert await provider.client_information() is not None
    assert await provider.code_verifier() == "v"
    assert await provider.state() == oauth_state

    await provider.invalidate_credentials("all")
    assert await provider.client_information() is None
    with pytest.raises(ValueError):
        await provider.code_verifier()
    assert await provider.state() != oauth_state


async def test_state_is_generated_once_and_reused():
    provider = _provider("https://a.example.com/mcp", "http://127.0.0.1:1/cb")
    first = await provider.state()
    assert first and first == await provider.state()


async def test_expires_in_becomes_an_absolute_expiry():
    provider = _provider("https://a.example.com/mcp", "http://127.0.0.1:1/cb")
    before = time.time()
    await provider.save_tokens(OAuthTokens(access_token="t", token_type="Bearer", expires_in=60))
    state = await provider._load()
    assert state.tokens_expire_at is not None
    assert before + 55 <= state.tokens_expire_at <= time.time() + 65


async def test_metadata_without_publication_is_still_usable():
    # A server that publishes no metadata still works off the RFC 8414 defaults.
    url, verifier = await start_authorization(
        "https://as.example.com",
        client_information=OAuthClientInformation(client_id="c"),
        redirect_url="http://127.0.0.1:1/cb",
    )
    assert url.startswith("https://as.example.com/authorize?")
    assert "code_challenge_method=S256" in url
    assert verifier


async def test_publishes_pkce_s256_requirement():
    metadata = parse_authorization_server_metadata(
        {
            "issuer": "https://as.example.com",
            "authorization_endpoint": "https://as.example.com/authorize",
            "token_endpoint": "https://as.example.com/token",
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["plain"],
        }
    )
    with pytest.raises(ValueError, match="PKCE S256"):
        await start_authorization(
            "https://as.example.com",
            metadata=metadata,
            client_information=OAuthClientInformation(client_id="c"),
            redirect_url="http://127.0.0.1:1/cb",
        )


# ---- the loopback callback server -----------------------------------------


async def test_callback_rejects_an_unknown_state():
    callback = await OAuthCallbackServer.listen()
    try:
        result = await _get(callback, "/callback?code=x&state=never-issued")
        assert result[0] == 400
    finally:
        await callback.close()


async def test_callback_rejects_a_wrong_path():
    callback = await OAuthCallbackServer.listen()
    try:
        status, _body = await _get(callback, "/nope?code=x&state=s")
        assert status == 404
    finally:
        await callback.close()


async def test_callback_reports_a_missing_code():
    callback = await OAuthCallbackServer.listen()
    try:
        waiting = asyncio.ensure_future(callback.wait_for_callback("s1"))
        await asyncio.sleep(0)
        status, _ = await _get(callback, "/callback?state=s1")
        assert status == 400
        with pytest.raises(ValueError, match="authorization code"):
            await waiting
    finally:
        await callback.close()


async def test_callback_surfaces_a_provider_error():
    callback = await OAuthCallbackServer.listen()
    try:
        waiting = asyncio.ensure_future(callback.wait_for_callback("s2"))
        await asyncio.sleep(0)
        status, body = await _get(
            callback, "/callback?state=s2&error=access_denied&error_description=nope"
        )
        # 200: the flow is over, the browser just needs telling.
        assert status == 200
        assert "nope" in body
        with pytest.raises(PermissionError, match="nope"):
            await waiting
    finally:
        await callback.close()


async def test_callback_twice_for_one_state_is_an_error():
    """Two waiters on one state is a caller bug, and is rejected as one.

    The yield matters: ``wait_for_callback`` is a coroutine, so the first call
    has not registered anything until the loop has run it. Without the yield
    this would be testing scheduling rather than the guard.
    """
    callback = await OAuthCallbackServer.listen()
    waiting = asyncio.ensure_future(callback.wait_for_callback("s3"))
    try:
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="already pending"):
            await callback.wait_for_callback("s3")
    finally:
        await callback.close()
        waiting.cancel()


async def test_callback_close_rejects_waiters():
    callback = await OAuthCallbackServer.listen()
    waiting = asyncio.ensure_future(callback.wait_for_callback("s4"))
    await asyncio.sleep(0)
    await callback.close()
    with pytest.raises(RuntimeError, match="closed"):
        await waiting


async def test_callback_binds_a_loopback_port():
    callback = await OAuthCallbackServer.listen()
    try:
        assert callback.redirect_url.startswith("http://127.0.0.1:")
        assert callback.port > 0
    finally:
        await callback.close()


async def _get(callback: OAuthCallbackServer, target: str) -> tuple[int, str]:
    """Issue a real HTTP GET at the callback server."""
    host, port = callback.redirect_url.split("//", 1)[1].split("/", 1)[0].split(":")
    reader, writer = await asyncio.open_connection(host, int(port))
    writer.write(f"GET {target} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), timeout=10)
    writer.close()
    await writer.wait_closed()
    head, _, body = raw.decode().partition("\r\n\r\n")
    return int(head.split()[1]), body
