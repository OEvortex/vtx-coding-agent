"""Block destructive bash commands before they run.

Subscribes to the ``tool_call`` event and returns ``{"block": True, ...}``
if the LLM tries to call ``bash`` with ``rm -rf``, ``sudo``, or
``dd of=/dev/`` style destructive patterns. The LLM sees the block
reason in the tool result and can try a safer alternative.
"""

from __future__ import annotations

import re

from vtx.extensions import TOOL_CALL

# Conservative patterns; tune to taste. Each pattern matches anywhere
# in the command (we do not try to tokenize shell — that is its own
# rabbit hole).
_DESTRUCTIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+(-[a-zA-Z]*[rR][fF][a-zA-Z]*|--recursive)\b"),
    re.compile(r"\brm\s+-[a-zA-Z]*[fF][rR][a-zA-Z]*\b"),
    re.compile(r"(\b|^)sudo\b"),
    re.compile(r"\bdd\s+.*\bof=/dev/(sd|hd|nvme|vd)"),
    re.compile(r":\(\)\s*\{.*:\|:.*\}"),  # fork bomb
    re.compile(r"\bchmod\s+(-R\s+)?(0?[0-7]{3,4})\b\s+/"),  # chmod 000 /
    re.compile(r"\bmkfs(\.[a-z0-9]+)?\s+/dev/"),
)

_BLOCK_REASON = (
    "Blocked by permission_gate extension: this command matches a destructive pattern. "
    "If you really need it, ask the user to run it manually or remove the extension."
)


def _is_destructive(command: str) -> bool:
    return any(p.search(command) for p in _DESTRUCTIVE_PATTERNS)


def register(api):
    @api.on(TOOL_CALL)
    def _gate(event, payload):
        if payload.get("tool_name") != "bash":
            return None
        args = payload.get("args") or {}
        command = args.get("command") or ""
        if not isinstance(command, str) or not command.strip():
            return None
        if _is_destructive(command):
            api.notify(f"blocked destructive bash: {command[:80]!r}", level="warning")
            return {"block": True, "reason": _BLOCK_REASON}
        return None
