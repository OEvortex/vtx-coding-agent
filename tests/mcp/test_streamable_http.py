import asyncio
import json
import threading

import pytest

from tests.mcp.http_helpers import Reply, Request, protocol_reply, serve, shutdown
from vtx.mcp import McpClient, McpClientOptions, McpError, McpRequestOptions
from vtx.mcp.transports.streamable_http import (
    McpAuthRequiredError,
    McpHttpError,
    McpSessionExpiredError,
    ReconnectOptions,
    StreamableHttpTransport,
    StreamableHttpTransportOptions,
)
from vtx.mcp.types import LATEST_PROTOCOL_VERSION


@pytest.fixture
def server():
    started: list = []

    def _start(handler):
        srv, url = serve(handler)
        started.append(srv)
        return srv, url

    yield _start
    for srv in started:
        shutdown(srv)


def _client(name: str = "http-test") -> McpClient:
    return McpClient(McpClientOptions(name=name, version="1.0.0"))


def _transport(url: str, **kwargs) -> StreamableHttpTransport:
    return StreamableHttpTransport(StreamableHttpTransportOptions(url=url, **kwargs))


# ---- the happy path -------------------------------------------------------


@pytest.mark.asyncio
async def test_handles_json_and_sse_replies_with_session_and_protocol_headers(server):
    srv, url = server(protocol_reply)
    transport = _transport(url)
    client = _client()

    await client.connect(transport)
    assert transport.session_id == "session-1"
    assert await client.list_tools() == [{"name": "echo", "inputSchema": {"type": "object"}}]
    assert await client.call_tool("echo", {"text": "hello"}) == {
        "content": [{"type": "text", "text": "hello"}]
    }
    await client.close()

    list_request = next(r for r in srv.requests if (r.message or {}).get("method") == "tools/list")
    assert list_request.headers["mcp-session-id"] == "session-1"
    assert list_request.headers["mcp-protocol-version"] == LATEST_PROTOCOL_VERSION
    # The GET stream is opened only after the session exists, and DELETE ends it.
    methods = [r.method for r in srv.requests]
    assert "GET" in methods
    assert "DELETE" in methods
    get_index = methods.index("GET")
    initialized_index = next(
        i
        for i, r in enumerate(srv.requests)
        if (r.message or {}).get("method") == "notifications/initialized"
    )
    assert get_index > initialized_index


@pytest.mark.asyncio
async def test_a_get_stream_can_be_disabled(server):
    srv, url = server(protocol_reply)
    client = _client()
    await client.connect(_transport(url, open_get_stream=False))
    await client.close()

    assert not any(r.method == "GET" for r in srv.requests)


@pytest.mark.asyncio
async def test_last_event_id_is_only_sent_when_resuming(server):
    srv, url = server(protocol_reply)
    client = _client()
    await client.connect(_transport(url))
    await client.call_tool("echo")
    await client.list_tools()
    await client.close()

    # Nothing dropped, so there is nothing to resume and the header must be
    # absent rather than empty.
    assert all("last-event-id" not in r.headers for r in srv.requests)


# ---- failure classification -----------------------------------------------


@pytest.mark.asyncio
async def test_a_401_is_reported_as_needing_authentication(server):
    def handler(request: Request) -> Reply:
        return Reply(
            status=401,
            headers={"www-authenticate": 'Bearer resource_metadata="https://example.com/meta"'},
            json_body={"error": "login required"},
        )

    _srv, url = server(handler)
    client = _client()

    with pytest.raises(McpAuthRequiredError) as excinfo:
        await client.connect(_transport(url))

    assert excinfo.value.status == 401
    assert excinfo.value.www_authenticate == 'Bearer resource_metadata="https://example.com/meta"'
    assert "login required" in excinfo.value.body


@pytest.mark.asyncio
async def test_an_http_error_includes_the_body(server):
    def handler(request: Request) -> Reply:
        return Reply(status=400, json_body={"error": "Invalid Accept header"})

    _srv, url = server(handler)
    with pytest.raises(McpHttpError) as excinfo:
        await _client().connect(_transport(url))

    assert "MCP HTTP request failed with status 400" in str(excinfo.value)
    assert "Invalid Accept header" in str(excinfo.value)


@pytest.mark.asyncio
async def test_an_expired_session_is_distinguished_from_a_plain_404(server):
    posts = {"n": 0}

    def handler(request: Request) -> Reply:
        if request.method == "POST":
            # Checked before incrementing, so the third POST -- tools/list --
            # is the one that 404s. Failing the second would break the
            # notifications/initialized handshake instead.
            if posts["n"] >= 2:
                return Reply(status=404, json_body={"error": "gone"})
            posts["n"] += 1
        return protocol_reply(request)

    _srv, url = server(handler)
    client = _client()
    await client.connect(_transport(url, open_get_stream=False))

    # The server provably did not run it, so the caller can retry on a fresh
    # session. A generic 404 would not carry that guarantee.
    with pytest.raises(McpSessionExpiredError):
        await client.list_tools()
    await client.close()


@pytest.mark.asyncio
async def test_a_404_before_a_session_exists_is_a_plain_error(server):
    def handler(request: Request) -> Reply:
        return Reply(status=404, json_body={"error": "nope"})

    _srv, url = server(handler)
    with pytest.raises(McpHttpError) as excinfo:
        await _client().connect(_transport(url))
    assert not isinstance(excinfo.value, McpSessionExpiredError)


@pytest.mark.asyncio
async def test_a_request_accepted_without_a_response_is_an_error(server):
    def handler(request: Request) -> Reply:
        message = request.message
        if request.method != "POST":
            return protocol_reply(request)
        if (message or {}).get("method") == "tools/call":
            return Reply(status=202)
        return protocol_reply(request)

    _srv, url = server(handler)
    client = _client()
    await client.connect(_transport(url, open_get_stream=False))

    with pytest.raises(McpHttpError, match="without a response"):
        await client.call_tool("echo")
    await client.close()


@pytest.mark.asyncio
async def test_an_unsupported_content_type_is_reported(server):
    def handler(request: Request) -> Reply:
        message = request.message
        if request.method != "POST":
            return protocol_reply(request)
        if (message or {}).get("method") == "tools/list":
            return Reply(status=200, headers={"content-type": "text/plain"}, json_body={"a": 1})
        return protocol_reply(request)

    _srv, url = server(handler)
    client = _client()
    await client.connect(_transport(url, open_get_stream=False))

    with pytest.raises(McpHttpError, match="Unsupported MCP response content type"):
        await client.list_tools()
    await client.close()


# ---- stream resilience ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_dropped_response_stream_fails_only_its_own_request(server):
    release = threading.Event()

    def handler(request: Request) -> Reply:
        message = request.message
        if request.method != "POST":
            return protocol_reply(request)
        if (message or {}).get("method") != "tools/call":
            return protocol_reply(request)
        name = ((message or {}).get("params") or {}).get("name")
        if name == "broken":
            return Reply(sse_lines=["data: not json"])
        if name == "slow":
            release.wait(10)
        return protocol_reply(request)

    _srv, url = server(handler)
    client = _client()
    errors: list = []
    client.on_error(errors.append)
    await client.connect(_transport(url, open_get_stream=False))

    slow = asyncio.ensure_future(client.call_tool("slow"))
    with pytest.raises(McpError, match="MCP response stream failed"):
        await client.call_tool("broken")
    release.set()

    assert (await slow) == {"content": [{"type": "text", "text": "hello"}]}
    assert errors
    await client.close()


@pytest.mark.asyncio
async def test_a_response_stream_that_ends_without_answering_fails_the_request(server):
    def handler(request: Request) -> Reply:
        message = request.message
        if request.method != "POST":
            return protocol_reply(request)
        if (message or {}).get("method") != "tools/call":
            return protocol_reply(request)
        return Reply(sse_lines=[": nothing here"])

    _srv, url = server(handler)
    client = _client()
    await client.connect(_transport(url, open_get_stream=False))

    with pytest.raises(McpError, match="stream ended without a response"):
        await client.call_tool("echo", {}, McpRequestOptions(timeout_ms=3000))
    await client.close()


@pytest.mark.asyncio
async def test_a_dropped_response_stream_is_resumed_with_last_event_id(server):
    resume_headers: list[str | None] = []

    def handler(request: Request) -> Reply:
        if request.method == "GET" and request.headers.get("last-event-id"):
            resume_headers.append(request.headers["last-event-id"])
            payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"content": [{"type": "text", "text": "resumed"}]},
            }
            return Reply(sse_lines=["id: 2", f"data: {json.dumps(payload)}"])
        message = request.message
        if request.method != "POST":
            return protocol_reply(request)
        if (message or {}).get("method") != "tools/call":
            return protocol_reply(request)
        # A priming event (id, no data) and a retry hint, then the stream drops.
        return Reply(sse_lines=["id: 1", "retry: 5", "data:"])

    _srv, url = server(handler)
    client = _client()
    errors: list = []
    client.on_error(errors.append)
    await client.connect(_transport(url, open_get_stream=False))

    assert await client.call_tool("echo") == {"content": [{"type": "text", "text": "resumed"}]}
    assert resume_headers == ["1"]
    assert errors == []
    await client.close()


@pytest.mark.asyncio
async def test_the_get_stream_reconnects_after_it_drops(server):
    gets = {"n": 0}
    last_event_ids: list[str | None] = []
    saw_two = threading.Event()

    def handler(request: Request) -> Reply:
        if request.method != "GET":
            return protocol_reply(request)
        gets["n"] += 1
        last_event_ids.append(request.headers.get("last-event-id"))
        notification = json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        if gets["n"] == 1:
            return Reply(sse_lines=["id: g1", f"data: {notification}"])
        saw_two.set()
        return Reply(sse_lines=["id: g2", f"data: {notification}"])

    _srv, url = server(handler)
    client = _client()
    seen: list = []

    def on_change(_params):
        seen.append(1)
        if len(seen) == 2:
            saw_two.set()

    client.on_notification("notifications/tools/list_changed", on_change)
    await client.connect(_transport(url, reconnect=ReconnectOptions(initial_delay_ms=1)))

    for _ in range(200):
        if len(seen) >= 2:
            break
        await asyncio.sleep(0.02)

    assert len(seen) >= 2
    # The second attempt resumes from the first stream's last event id.
    assert last_event_ids[:2] == [None, "g1"]
    await client.close()


@pytest.mark.asyncio
async def test_a_server_with_no_get_stream_is_tolerated(server):
    # protocol_reply answers GET with 405, which the transport reads as
    # "this server does not offer a server-to-client stream".
    _srv, url = server(protocol_reply)
    client = _client()
    await client.connect(_transport(url))
    assert await client.list_tools() == [{"name": "echo", "inputSchema": {"type": "object"}}]
    await client.close()


# ---- auth -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_401_then_insufficient_scope_403_are_handed_to_the_auth_provider(server):
    seen: list[dict] = []
    token = {"value": "old"}

    def handler(request: Request) -> Reply:
        message = request.message
        if request.method != "POST":
            return protocol_reply(request)
        auth = request.headers.get("authorization")
        if auth == "Bearer old":
            return Reply(status=401, headers={"www-authenticate": "Bearer"})
        if (message or {}).get("method") == "tools/call" and auth == "Bearer new":
            return Reply(
                status=403,
                headers={"www-authenticate": 'Bearer error="insufficient_scope", scope="admin"'},
            )
        return protocol_reply(request)

    class Provider:
        async def token(self):
            return token["value"]

        async def on_unauthorized(self, context):
            seen.append({"status": context.response.status_code, "token": context.token})
            token["value"] = "new" if context.response.status_code == 401 else "admin"

    _srv, url = server(handler)
    client = _client()
    await client.connect(_transport(url, open_get_stream=False, auth_provider=Provider()))

    assert await client.call_tool("echo") == {"content": [{"type": "text", "text": "hello"}]}
    # Both challenges reached the provider, each carrying the token it rejected.
    assert seen == [{"status": 401, "token": "old"}, {"status": 403, "token": "new"}]
    await client.close()


@pytest.mark.asyncio
async def test_a_bearer_token_is_sent_on_every_request(server):
    srv, url = server(protocol_reply)
    client = _client()

    class Provider:
        async def token(self):
            return "tok"

    await client.connect(_transport(url, open_get_stream=False, auth_provider=Provider()))
    await client.close()

    posts = [r for r in srv.requests if r.method == "POST"]
    assert posts
    assert all(r.headers["authorization"] == "Bearer tok" for r in posts)
