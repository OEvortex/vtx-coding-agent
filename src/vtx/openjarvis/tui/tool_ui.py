"""Jarvis-style tool UI metadata for OpenJarvis tools.

Central place for how OpenJarvis tools present themselves in the TUI:
per-tool icons, human-friendly call formatting, approval previews, and
result summaries. Used by the harness adapter (tool blocks) and the
welcome/loaded-resources banner (branding).
"""

from __future__ import annotations

from typing import Any

# Per-tool glyphs shown next to tool calls in the TUI.
TOOL_ICONS: dict[str, str] = {
    "exec": "❯",  # noqa: RUF001
    "apply_patch": "✎",
    "cron": "⏱",
    "message": "✉",
    "list_exec_sessions": "▦",
    "write_stdin": "↳",
    "long_task": "◈",
    "complete_goal": "◉",
    "run_cli_app": "▣",
    "generate_image": "✧",
    "my": "✦",
}

DEFAULT_ICON = "→"

_SUMMARY_MAX_CHARS = 140
_CALL_MAX_CHARS = 160


def tool_icon(name: str, oj_tool: Any = None) -> str:
    """Icon for a tool: per-tool override, known-name map, then default."""
    if oj_tool is not None:
        icon = getattr(oj_tool, "tool_icon", None)
        if icon:
            return str(icon)
    return TOOL_ICONS.get(name, DEFAULT_ICON)


def _trunc(value: Any, limit: int = _SUMMARY_MAX_CHARS) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _first(data: dict, *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return None


def format_call(name: str, data: dict[str, Any]) -> str:
    """Human-friendly one-line call text shown on the tool header."""
    if not isinstance(data, dict):
        return _trunc(data)

    if name == "exec":
        return _trunc(data.get("command"), 160)

    if name == "apply_patch":
        edits = data.get("edits") or []
        if isinstance(edits, dict):
            edits = [edits]
        actions: list[str] = []
        paths: list[str] = []
        for edit in edits if isinstance(edits, list) else []:
            if isinstance(edit, dict):
                actions.append(str(edit.get("action", "?")))
                path = edit.get("path")
                if path:
                    paths.append(str(path))
        bits = []
        if paths:
            shown = ", ".join(paths[:3]) + (f" +{len(paths) - 3}" if len(paths) > 3 else "")
            bits.append(shown)
        if actions:
            bits.append(f"{len(actions)} edit(s)")
        if data.get("dry_run"):
            bits.append("dry-run")
        return " · ".join(bits)

    if name == "cron":
        bits = [str(data.get("action", "?"))]
        if data.get("name"):
            bits.append(f"'{_trunc(data['name'], 40)}'")
        if data.get("every_seconds"):
            bits.append(f"every {data['every_seconds']}s")
        if data.get("cron_expr"):
            bits.append(f"cron '{data['cron_expr']}'")
        if data.get("at"):
            bits.append(f"at {data['at']}")
        return " ".join(bits)

    if name == "message":
        bits = []
        if data.get("channel"):
            bits.append(str(data["channel"]))
        if data.get("chat_id"):
            bits.append(str(data["chat_id"]))
        text = _trunc(data.get("message") or data.get("content"), 80)
        if text:
            bits.append(text)
        return " → ".join(bits) if bits else ""

    if name == "write_stdin":
        bits = []
        if data.get("session_id"):
            bits.append(f"[{_trunc(data['session_id'], 12)}]")
        if data.get("chars"):
            bits.append(_trunc(data["chars"], 80))
        flags = [f for f in ("close_stdin", "terminate") if data.get(f)]
        if flags:
            bits.append(f"({', '.join(flags)})")
        return " ".join(bits)

    if name == "long_task":
        return _trunc(data.get("goal") or data.get("objective"), 120)

    if name == "complete_goal":
        return _trunc(data.get("recap"), 120)

    if name == "run_cli_app":
        bits = [str(data.get("app") or data.get("name") or "?")]
        args = data.get("args") or []
        if args:
            bits.append(" ".join(str(a) for a in args))
        return _trunc(" ".join(bits), 140)

    if name in ("list_exec_sessions",):
        return ""

    # Generic fallback: skip noisy keys, join the rest.
    skip = {"dry_run", "timeout", "yield_time_ms", "json", "working_dir"}
    parts = [f"{k}={_trunc(v, 60)}" for k, v in data.items() if k not in skip and v is not None]
    return " / ".join(parts)[:_CALL_MAX]


_CALL_MAX = 200


def format_preview(name: str, data: dict[str, Any]) -> str | None:
    """Approval-time preview (only shown for tools that need approval)."""
    call = format_call(name, data)
    if not call:
        return None
    if name == "exec":
        return f"$ {call}"
    return call


def build_result_ui(result: str, success: bool) -> dict[str, str | None]:
    """Split a raw tool result into ``ui_summary`` / ``ui_details`` / ``ui_details_full``.

    The summary is the first meaningful line (error line first on failure); the
    details carry the remaining body so the header stays one clean line.
    """
    if not result:
        return {"ui_summary": None, "ui_details": None, "ui_details_full": None}
    lines = [line for line in result.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return {"ui_summary": None, "ui_details": None, "ui_details_full": None}

    first = lines[0].strip()
    rest = "\n".join(lines[1:]).strip()
    if not success:
        first = f"✗ {first}" if not first.startswith("Error") else first
    summary = _trunc(first)
    details = _trunc(rest, 800) if rest else None
    full = rest if rest else None
    return {"ui_summary": summary, "ui_details": details, "ui_details_full": full}


__all__ = [
    "DEFAULT_ICON",
    "TOOL_ICONS",
    "build_result_ui",
    "format_call",
    "format_preview",
    "tool_icon",
]
