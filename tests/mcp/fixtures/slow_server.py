"""A server whose ``slow`` tool never returns, for cancellation and timeout tests.

``initialize`` is answered normally; ``tools/call`` for ``slow`` is simply never
answered, so the client has to give up on it.
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    print("slow fixture ready", file=sys.stderr, flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        method = message.get("method")
        if "id" not in message:
            continue
        if method == "initialize":
            reply = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "slow-fixture", "version": "1.0.0"},
            }
        elif method == "tools/call":
            continue  # never answered
        elif method == "ping":
            reply = {}
        else:
            sys.stdout.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32601, "message": "not found"},
                    }
                )
                + "\n"
            )
            sys.stdout.flush()
            continue
        sys.stdout.write(
            json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": reply}) + "\n"
        )
        sys.stdout.flush()


if __name__ == "__main__":
    main()
