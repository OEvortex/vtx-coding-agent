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


def render_tree(lines: list[str], max_lines: int = 10) -> str:
    """Boxless pi-style card: ``├─`` rows with a ``└─`` tail and a `… N more` hint.

    Structure survives without color — plain tree prefixes carry the shape.
    """
    if not lines:
        return ""
    extra = max(len(lines) - max_lines, 0)
    shown = lines[:max_lines] if extra else lines
    rows: list[str] = []
    for i, line in enumerate(shown):
        last_visible = i == len(shown) - 1 and not extra
        rows.append(f"{'└─' if last_visible else '├─'} {line}")
    if extra:
        rows.append(f"   … +{extra} lines (ctrl+o to expand)")
    return "\n".join(rows)


def build_result_ui(
    result: str, success: bool, elapsed_s: float | None = None
) -> dict[str, str | None]:
    """Split a raw tool result into pi-style UI fields.

    - ``ui_summary``: first meaningful line (one clean header line; ``✗``
      prefix on failure).
    - ``ui_details``: boxless tree card of the remaining output plus a
      metrics tail (``╰─ ⏱ 0.8s · 12 lines``); single-line results stay
      header-only.
    - ``ui_details_full``: the complete output for ctrl+o expansion.
    """
    if not result:
        return {"ui_summary": None, "ui_details": None, "ui_details_full": None}
    lines = result.replace("\r\n", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return {"ui_summary": None, "ui_details": None, "ui_details_full": None}

    first = lines[0].strip()
    if not success:
        first = f"✗ {first}" if not first.startswith("Error") else first
    summary = _trunc(first)

    if len(lines) == 1:
        return {"ui_summary": summary, "ui_details": None, "ui_details_full": None}

    metrics_bits: list[str] = []
    if elapsed_s is not None:
        metrics_bits.append(f"⏱ {elapsed_s:.1f}s")
    metrics_bits.append(f"{len(lines)} lines")
    metrics = "╰─ " + " · ".join(metrics_bits)

    body = render_tree(lines[1:])
    details = f"{body}\n{metrics}" if body else metrics
    full = "\n".join(lines[1:])
    return {"ui_summary": summary, "ui_details": details, "ui_details_full": full}


__all__ = [
    "DEFAULT_ICON",
    "TOOL_ICONS",
    "build_result_ui",
    "format_call",
    "format_preview",
    "render_tree",
    "tool_icon",
]
