"""A loopback HTTP server for Streamable HTTP transport tests.

Deliberately dumb: a handler receives the parsed JSON-RPC message and the
:class:`http.server.BaseHTTPRequestHandler`, and writes the response. That is
enough to drive the transport through every path it has to handle -- JSON
replies, SSE replies, dropped streams, resumptions, 401s, expired sessions --
without the server logic obscuring what the transport is doing.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from vtx.mcp.types import LATEST_PROTOCOL_VERSION

Handler = Callable[["Request"], "Reply"]


@dataclass
class Request:
    method: str
    path: str
    headers: dict[str, str]
    message: dict[str, Any] | None = None


@dataclass
class Reply:
    """What the handler wants sent back.

    ``sse_lines`` writes a raw SSE body; ``json_body`` a JSON one. Leaving both
    empty gives the 202 acknowledgement the spec uses for notifications.
    """

    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    json_body: Any = None
    sse_lines: list[str] | None = None
    # Held open instead of ended, so the client's reconnect logic sees a stream
    # that stays up.
    keep_open: bool = False


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[Request] = []
        self.keep_open_response = False
        super().__init__(("127.0.0.1", 0), _RequestHandler)


class _RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass

    def _read_message(self) -> dict[str, Any] | None:
        length = int(self.headers.get("content-length") or 0)
        if not length:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            return None

    def _dispatch(self, method: str) -> None:
        message = self._read_message() if method == "POST" else None
        request = Request(
            method=method,
            path=self.path.split("?", 1)[0],
            headers={k.lower(): v for k, v in self.headers.items()},
            message=message,
        )
        self.server.requests.append(request)  # ty: ignore[attr-defined]

        reply = self.server.handler(request)  # ty: ignore[attr-defined]

        if reply.sse_lines is not None:
            self.send_response(reply.status)
            for key, value in reply.headers.items():
                self.send_header(key, value)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.send_header("connection", "keep-alive" if reply.keep_open else "close")
            self.end_headers()
            for line in reply.sse_lines:
                self.wfile.write((line + "\n\n").encode())
                self.wfile.flush()
            if reply.keep_open:
                # Park until the client goes away.
                threading.Event().wait(30)
            else:
                # HTTP/1.1 with no content-length or chunking means the body
                # ends when the connection closes, so a "dropped" SSE stream
                # must actually close. Leaving it open makes the client wait
                # forever, which is a different bug than the one under test.
                self.close_connection = True
            return

        if reply.json_body is not None:
            body = json.dumps(reply.json_body).encode()
            # Popped before the loop: mutating headers while iterating them
            # both skips and duplicates entries.
            content_type = reply.headers.pop("content-type", "application/json")
            self.send_response(reply.status)
            for key, value in reply.headers.items():
                self.send_header(key, value)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(reply.status)
        for key, value in reply.headers.items():
            self.send_header(key, value)
        self.send_header("content-length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")


def serve(handler: Handler) -> tuple[_Server, str]:
    """Start a loopback server and return ``(server, url)``."""
    server = _Server(handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}/mcp"


def shutdown(server: _Server) -> None:
    server.shutdown()
    server.server_close()


# ---- a default protocol handler -------------------------------------------


def protocol_reply(request: Request) -> Reply:
    """The common case: a server that speaks plain JSON."""
    message = request.message
    if request.method == "GET":
        return Reply(status=405)
    if request.method == "DELETE":
        return Reply(status=200)
    if message is None or "id" not in message:
        return Reply(status=202)

    method = message.get("method")
    if method == "initialize":
        return Reply(
            headers={"mcp-session-id": "session-1"},
            json_body={
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "protocolVersion": LATEST_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "http-fixture", "version": "1.0.0"},
                },
            },
        )
    if method == "tools/list":
        return Reply(
            json_body={
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]},
            }
        )
    if method == "tools/call":
        return Reply(
            sse_lines=[
                "id: tool-result",
                f"data: {json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': {'content': [{'type': 'text', 'text': 'hello'}]}})}",
            ]
        )
    return Reply(
        json_body={
            "jsonrpc": "2.0",
            "id": message["id"],
            "error": {"code": -32601, "message": "not found"},
        }
    )
