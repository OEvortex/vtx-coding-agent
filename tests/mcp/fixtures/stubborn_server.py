"""A server that ignores shutdown.

Answers ``initialize``, spawns a grandchild that also ignores SIGTERM, then
keeps running. Exercises the transport's process-group kill: closing must take
out the grandchild too, or it is orphaned holding the pipe open.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys

INITIALIZE_RESULT = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "serverInfo": {"name": "stubborn-fixture", "version": "1.0.0"},
}


def main() -> None:
    # SIGTERM is ignored, so only the SIGKILL escalation can end this.
    signal.signal(signal.SIGTERM, lambda *_: None)

    grandchild = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-c",
            "import signal,time\n"
            "signal.signal(signal.SIGTERM, lambda *_: None)\n"
            "while True: time.sleep(0.05)\n",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"grandchild {grandchild.pid}", file=sys.stderr, flush=True)

    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        if message.get("method") != "initialize" or "id" not in message:
            continue
        sys.stdout.write(
            json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": INITIALIZE_RESULT}) + "\n"
        )
        sys.stdout.flush()

    # Stdin closed but we deliberately keep running.
    while True:
        pass


if __name__ == "__main__":
    main()
