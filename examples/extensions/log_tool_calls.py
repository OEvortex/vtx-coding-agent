"""Log every tool call to a JSONL file in the agent dir.

This is the simplest possible observability extension: a single
``tool_call`` handler that appends a structured line for every LLM
tool invocation. Useful for post-hoc debugging, sharing sessions
with collaborators, or feeding tool-use data into a local eval.

The log lives at ``~/.vtx/agent/tool-calls.log``. Delete it or rotate
it yourself; the extension does not manage retention.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from vtx.extensions import TOOL_CALL, TOOL_RESULT

_LOG_PATH = Path.home() / ".vtx" / "agent" / "tool-calls.log"


def _log(record: dict) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def register(api):
    @api.on(TOOL_CALL)
    def _on_call(event, payload):
        _log(
            {
                "ts": datetime.now(UTC).isoformat(),
                "event": "call",
                "tool": payload.get("tool_name"),
                "tool_call_id": payload.get("tool_call_id"),
                "args": payload.get("args"),
            }
        )
        return None

    @api.on(TOOL_RESULT)
    def _on_result(event, payload):
        result = payload.get("result")
        _log(
            {
                "ts": datetime.now(UTC).isoformat(),
                "event": "result",
                "tool": payload.get("tool_name"),
                "tool_call_id": payload.get("tool_call_id"),
                "is_error": getattr(result, "is_error", None),
            }
        )
        return None
