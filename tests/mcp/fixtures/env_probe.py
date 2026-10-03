"""Reports the value of ``MCP_TEST_VALUE`` as the server version.

Lets a test assert that the transport's env handling works without adding
output to the shared fixture.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> None:
    version = os.environ.get("MCP_TEST_VALUE", "missing")
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        if message.get("method") != "initialize" or "id" not in message:
            continue
        sys.stdout.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "env-probe", "version": version},
                    },
                }
            )
            + "\n"
        )
        sys.stdout.flush()
        return


if __name__ == "__main__":
    main()
