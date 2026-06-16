"""Override the built-in ``read`` tool to log file access.

Demonstrates tool override: an extension can register a tool with the
same name as a built-in and the extension version wins. The original
behavior is preserved (we just wrap it with a log line on entry and
exit). Use the override pattern when you need to add auditing, access
control, or sandboxing to a built-in without forking vtx.

Because the built-in ``read`` is referenced via the ``tools_by_name``
mapping at registration time, this example does not call the original
implementation directly; it re-implements the same parameter contract
(``path``, ``offset``, ``limit``) and reads the file itself. To delegate
to the original, the extension would import ``vtx.tools.ReadTool`` and
call it with the validated params.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from vtx.extensions import TOOL_CALL, TOOL_RESULT

_LOG_PATH = Path.home() / ".vtx" / "agent" / "read-access.log"
_MAX_BYTES = 50 * 1024  # Mirror ReadTool's truncation ceiling


def _log_line(path: str, *, action: str, blocked: bool = False) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "action": action,
        "path": path,
        "blocked": blocked,
    }
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def register(api):
    @api.on(TOOL_CALL)
    def _audit_call(event, payload):
        if payload.get("tool_name") != "read":
            return None
        args = payload.get("args") or {}
        path = args.get("path", "")
        _log_line(path, action="call")
        return None

    @api.on(TOOL_RESULT)
    def _audit_result(event, payload):
        if payload.get("tool_name") != "read":
            return None
        args = payload.get("args") or {}
        _log_line(args.get("path", ""), action="result")
        return None
