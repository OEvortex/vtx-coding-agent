"""A newline-delimited MCP server on stdin/stdout, for stdio transport tests.

Run as a script: ``python stdio_server.py``. Writes a banner to stderr so the
test can prove stderr is captured separately from the protocol stream.
"""

from __future__ import annotations

import json
import sys

INITIALIZE_RESULT = {
    "protocolVersion": "2025-06-18",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "stdio-fixture", "version": "1.0.0"},
}


def handle(message: dict) -> dict:
    method = message.get("method")
    if method == "initialize":
        return INITIALIZE_RESULT
    if method == "tools/list":
        return {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
    if method == "tools/call":
        text = (message.get("params", {}).get("arguments") or {}).get("text")
        return {"content": [{"type": "text", "text": str(text)}]}
    if method == "ping":
        return {}
    raise KeyError("not found")


def main() -> None:
    print("stdio fixture ready", file=sys.stderr, flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        if "id" not in message:
            continue  # a notification; nothing to answer
        try:
            result = handle(message)
        except KeyError:
            reply = {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {"code": -32601, "message": "not found"},
            }
        else:
            reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
