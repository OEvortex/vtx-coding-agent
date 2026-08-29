"""Jarvis-style tool UI metadata and renderers for OpenJarvis tools.

Central place for how OpenJarvis tools present themselves in the TUI:
per-tool icons, human-friendly call formatting, approval previews,
boxed execution frames, and output trees matching the pi-style aesthetic.
"""

from __future__ import annotations

from typing import Any

# Per-tool glyphs matching the pi-style visual language
TOOL_ICONS: dict[str, str] = {
    "exec": "➔",
    "bash": "➔",
    "apply_patch": "➔",
    "edit": "➔",
    "edit_file": "➔",
    "write": "✎",
    "write_file": "✎",
    "read": "●",
    "read_file": "●",
    "find": "Q",
    "find_files": "Q",
    "list_dir": "Q",
    "ls": "Q",
    "grep": "Q",
    "cron": "⏱",
    "message": "✉",
    "list_exec_sessions": "▦",
    "write_stdin": "↳",
    "long_task": "◈",
    "task": "◈",
    "complete_goal": "◉",
    "goal": "◉",
    "run_cli_app": "▣",
    "generate_image": "✧",
    "my": "✦",
    "skill": "λ",
    "web": "🌐",
    "web_search": "🌐",
    "ask_user": "❓",
}

DEFAULT_ICON = "➔"

_SUMMARY_MAX_CHARS = 140
_CALL_MAX_CHARS = 160
_BOX_DEFAULT_WIDTH = 84


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


def format_call(name: str, data: dict[str, Any]) -> str:
    """Human-friendly one-line call text shown on the tool header."""
    if not isinstance(data, dict):
        return _trunc(data)

    if name in ("exec", "bash"):
        cmd = data.get("command") or data.get("cmd") or ""
        return _trunc(cmd, 160)

    if name in ("apply_patch", "edit", "edit_file"):
        path = (
            data.get("path")
            or data.get("file_path")
            or data.get("TargetFile")
            or data.get("filepath")
            or ""
        )
        edits = data.get("edits") or []
        if isinstance(edits, dict):
            edits = [edits]
        actions: list[str] = []
        paths: list[str] = [str(path)] if path else []
        for edit in edits if isinstance(edits, list) else []:
            if isinstance(edit, dict):
                actions.append(str(edit.get("action", "?")))
                p = edit.get("path")
                if p:
                    paths.append(str(p))
        bits = []
        if paths:
            shown = ", ".join(paths[:3]) + (f" +{len(paths) - 3}" if len(paths) > 3 else "")
            bits.append(shown)
        if actions:
            bits.append(f"{len(actions)} edit(s)")
        if data.get("dry_run"):
            bits.append("dry-run")
        return " · ".join(bits) if bits else str(path)

    if name in ("write", "write_file"):
        path = data.get("path") or data.get("file_path") or data.get("TargetFile") or ""
        return str(path)

    if name in ("read", "read_file"):
        path = data.get("path") or data.get("file_path") or data.get("AbsolutePath") or ""
        offset = data.get("offset") or data.get("StartLine")
        limit = data.get("limit") or data.get("EndLine")
        if offset is not None or limit is not None:
            return f"{path}:{offset or 1}-{limit or ''}"
        return str(path)

    if name in ("find", "find_files", "list_dir", "ls"):
        path = data.get("path") or data.get("dir_path") or data.get("SearchDirectory") or "."
        pattern = data.get("pattern") or data.get("Pattern") or ""
        if pattern:
            return f"{pattern} · in {path}"
        return str(path)

    if name == "grep":
        query = data.get("query") or data.get("Query") or data.get("pattern") or ""
        path = data.get("path") or data.get("SearchPath") or "."
        return f"{query} · in {path}" if query else str(path)

    if name in ("web", "web_search"):
        query = data.get("query") or data.get("q") or ""
        return _trunc(query, 120)

    if name == "skill":
        name_val = data.get("name") or data.get("skill_name") or ""
        return str(name_val)

    if name == "ask_user":
        questions = data.get("questions") or []
        if questions and isinstance(questions, list) and isinstance(questions[0], dict):
            return _trunc(questions[0].get("question", ""), 120)
        q = data.get("question") or data.get("prompt") or ""
        return _trunc(q, 120)

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

    if name in ("long_task", "task"):
        return _trunc(data.get("goal") or data.get("objective") or data.get("Prompt"), 120)

    if name in ("complete_goal", "goal"):
        return _trunc(data.get("recap") or data.get("title"), 120)

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
    return " / ".join(parts)[:200]


def format_preview(name: str, data: dict[str, Any]) -> str | None:
    """Approval-time preview (only shown for tools that need approval)."""
    call = format_call(name, data)
    if not call:
        return None
    if name in ("exec", "bash"):
        return f"$ {call}"
    return call


def render_tree(lines: list[str], max_lines: int = 8) -> str:
    """Boxless pi-style tree card: ``├─`` rows with a ``└─`` tail and a `… N more` hint."""
    if not lines:
        return ""
    clean_lines = [line_text for line_text in lines if line_text.strip()]
    if not clean_lines:
        return ""
    extra = max(len(clean_lines) - max_lines, 0)
    shown = clean_lines[:max_lines] if extra else clean_lines
    rows: list[str] = []
    for i, line in enumerate(shown):
        last_visible = i == len(shown) - 1 and not extra
        rows.append(f"{'  └─' if last_visible else '  ├─'} {line}")
    if extra:
        rows.append(f"  … +{extra} lines (Ctrl+O to expand)")
    return "\n".join(rows)


def _render_boxed_card(
    title: str,
    command: str,
    response_lines: list[str],
    success: bool = True,
    elapsed_s: float | None = None,
    width: int = _BOX_DEFAULT_WIDTH,
    max_body_lines: int = 10,
) -> tuple[str, str]:
    """Render a rounded pi-style boxed tool execution card.

    Returns (collapsed_card, full_card).
    """
    inner_width = max(40, width - 4)
    state_mark = "✓" if success else "✗"
    header_left = f"╭─ ➔ {title} {state_mark} "
    header_dashes = max(2, width - len(header_left) - 1)
    top_border = f"{header_left}{'─' * header_dashes}╮"

    # Command line
    cmd_text = f"$ {command}"
    if len(cmd_text) > inner_width:
        cmd_text = cmd_text[: inner_width - 1] + "…"

    divider_left = "├─ Response "
    divider_dashes = max(2, width - len(divider_left) - 1)
    divider = f"{divider_left}{'─' * divider_dashes}┤"

    # Clean response lines
    clean_resp = [line_text.rstrip() for line_text in response_lines]
    while clean_resp and not clean_resp[-1].strip():
        clean_resp.pop()
    if clean_resp and clean_resp[-1].strip().lower().startswith("exit code:"):
        clean_resp.pop()
    while clean_resp and not clean_resp[-1].strip():
        clean_resp.pop()

    # Stats footer
    exit_code = 0 if success else 1
    elapsed_str = f"{elapsed_s:.2f}s" if elapsed_s is not None else "0.04s"
    word_count = sum(len(line_text.split()) for line_text in clean_resp)
    footer_left = f"╰─ Exit {exit_code} · {elapsed_str} · ~{word_count} words "
    hint_right = " Ctrl+O for more ╯"
    needed_dashes = width - len(footer_left) - len(hint_right)
    if needed_dashes < 2:
        footer_dashes = 2
        bottom_border = f"{footer_left}{'─' * footer_dashes}╯"
    else:
        bottom_border = f"{footer_left}{'─' * needed_dashes}{hint_right}"

    def _build_frame(lines_to_show: list[str]) -> str:
        body: list[str] = [top_border, f"│ {cmd_text.ljust(inner_width)} │"]
        if lines_to_show:
            body.append(divider)
            for line in lines_to_show:
                truncated = line[:inner_width] if len(line) > inner_width else line
                body.append(f"│ {truncated.ljust(inner_width)} │")
        body.append(bottom_border)
        return "\n".join(body)

    if len(clean_resp) > max_body_lines:
        collapsed_resp = clean_resp[:max_body_lines]
        collapsed_resp.append(f"… ({len(clean_resp) - max_body_lines} more lines hidden)")
    else:
        collapsed_resp = clean_resp

    return _build_frame(collapsed_resp), _build_frame(clean_resp)


def _render_edit_card(
    path: str,
    diff_lines: list[str],
    success: bool = True,
    elapsed_s: float | None = None,
    width: int = _BOX_DEFAULT_WIDTH,
    max_body_lines: int = 12,
) -> tuple[str, str]:
    """Render a rounded pi-style boxed diff/edit card."""
    inner_width = max(40, width - 4)
    state_mark = "✓" if success else "✗"
    path_str = _trunc(path, 40)
    header_left = f"╭─ ➔ Edit {state_mark} · {path_str} "
    header_dashes = max(2, width - len(header_left) - 1)
    top_border = f"{header_left}{'─' * header_dashes}╮"

    additions = sum(1 for line in diff_lines if line.strip().startswith("+"))
    deletions = sum(1 for line in diff_lines if line.strip().startswith("-"))
    diff_info = (
        f"+{additions} -{deletions}" if (additions or deletions) else f"{len(diff_lines)} lines"
    )

    divider_left = f"├─ Diff · {diff_info} "
    divider_right = " Ctrl+O more ┤"
    needed_divider_dashes = width - len(divider_left) - len(divider_right)
    if needed_divider_dashes < 2:
        divider = f"{divider_left}{'─' * 2}┤"
    else:
        divider = f"{divider_left}{'─' * needed_divider_dashes}{divider_right}"

    elapsed_str = f" · {elapsed_s:.2f}s" if elapsed_s is not None else ""
    footer_left = f"╰─ 1 file · {diff_info}{elapsed_str} "
    footer_dashes = max(2, width - len(footer_left) - 1)
    bottom_border = f"{footer_left}{'─' * footer_dashes}╯"

    def _build_frame(lines_to_show: list[str]) -> str:
        body: list[str] = [top_border, divider]
        for line in lines_to_show:
            truncated = line[:inner_width] if len(line) > inner_width else line
            body.append(f"│ {truncated.ljust(inner_width)} │")
        body.append(bottom_border)
        return "\n".join(body)

    clean_lines = [line.rstrip() for line in diff_lines if line.strip()]
    if not clean_lines:
        clean_lines = ["(no textual differences)"]

    if len(clean_lines) > max_body_lines:
        collapsed_lines = clean_lines[:max_body_lines]
        collapsed_lines.append(f"… ({len(clean_lines) - max_body_lines} more lines hidden)")
    else:
        collapsed_lines = clean_lines

    return _build_frame(collapsed_lines), _build_frame(clean_lines)


def _render_write_card(
    path: str,
    content_lines: list[str],
    success: bool = True,
    elapsed_s: float | None = None,
    width: int = _BOX_DEFAULT_WIDTH,
    max_body_lines: int = 10,
) -> tuple[str, str]:
    """Render a rounded pi-style boxed file write card."""
    inner_width = max(40, width - 4)
    state_mark = "✓" if success else "✗"
    path_str = _trunc(path, 40)
    header_left = f"╭─ ✎ Write {state_mark} · {path_str} "
    header_dashes = max(2, width - len(header_left) - 1)
    top_border = f"{header_left}{'─' * header_dashes}╮"

    line_count = len(content_lines)
    divider_left = f"├─ Written {line_count} lines "
    divider_right = " Ctrl+O more ┤"
    needed_divider_dashes = width - len(divider_left) - len(divider_right)
    if needed_divider_dashes < 2:
        divider = f"{divider_left}{'─' * 2}┤"
    else:
        divider = f"{divider_left}{'─' * needed_divider_dashes}{divider_right}"

    elapsed_str = f" · {elapsed_s:.2f}s" if elapsed_s is not None else ""
    footer_left = f"╰─ 1 file · {line_count} lines{elapsed_str} "
    footer_dashes = max(2, width - len(footer_left) - 1)
    bottom_border = f"{footer_left}{'─' * footer_dashes}╯"

    def _build_frame(lines_to_show: list[str]) -> str:
        body: list[str] = [top_border, divider]
        for idx, line in enumerate(lines_to_show, start=1):
            numbered = f"{idx:>3}  {line}"
            truncated = numbered[:inner_width] if len(numbered) > inner_width else numbered
            body.append(f"│ {truncated.ljust(inner_width)} │")
        body.append(bottom_border)
        return "\n".join(body)

    clean_lines = [line.rstrip() for line in content_lines]
    if not clean_lines:
        clean_lines = ["(empty file)"]

    if len(clean_lines) > max_body_lines:
        collapsed_lines = clean_lines[:max_body_lines]
        collapsed_lines.append(f"… ({len(clean_lines) - max_body_lines} more lines hidden)")
    else:
        collapsed_lines = clean_lines

    return _build_frame(collapsed_lines), _build_frame(clean_lines)


def _render_file_tree(header: str, files: list[str], max_shown: int = 6) -> tuple[str, str]:
    """Boxless file tree matching List / Glob output from the reference image."""
    rows: list[str] = [header]
    clean_files = [f.strip() for f in files if f.strip()]
    if not clean_files:
        return header, header

    for file in clean_files[:max_shown]:
        prefix = "  A " if file.endswith("/") else "  * "
        rows.append(f"{prefix}{file}")

    if len(clean_files) > max_shown:
        rows.append(f"  … {len(clean_files) - max_shown} more files")

    full_rows = [header] + [f"{'  A ' if f.endswith('/') else '  * '}{f}" for f in clean_files]
    return "\n".join(rows), "\n".join(full_rows)


def build_result_ui(
    result: str,
    success: bool,
    elapsed_s: float | None = None,
    tool_name: str = "",
    tool_data: dict[str, Any] | None = None,
) -> dict[str, str | None]:
    """Split a raw tool result into pi-style UI fields.

    - ``ui_summary``: Header line or None if the card itself carries the header.
    - ``ui_details``: Rendered collapsed card or boxless tree.
    - ``ui_details_full``: Full output for Ctrl+O expansion.
    """
    if not result:
        return {"ui_summary": None, "ui_details": None, "ui_details_full": None}

    raw_lines = result.replace("\r\n", "\n").split("\n")
    while raw_lines and not raw_lines[0].strip():
        raw_lines.pop(0)
    while raw_lines and not raw_lines[-1].strip():
        raw_lines.pop()

    if not raw_lines:
        return {"ui_summary": None, "ui_details": None, "ui_details_full": None}

    data = tool_data or {}
    name_lower = tool_name.lower()

    # 1. Exec / Bash -> Boxed command frame
    if name_lower in ("exec", "bash", "run_cli_app"):
        cmd = (
            data.get("command")
            or data.get("cmd")
            or (data.get("app", "") + " " + " ".join(str(a) for a in data.get("args", [])))
            or "command"
        )
        collapsed_box, full_box = _render_boxed_card(
            title="Bash" if name_lower == "bash" else "Exec",
            command=str(cmd),
            response_lines=raw_lines,
            success=success,
            elapsed_s=elapsed_s,
        )
        return {"ui_summary": None, "ui_details": collapsed_box, "ui_details_full": full_box}

    # 2. Edit / Apply Patch -> Boxed diff card
    if name_lower in ("edit", "edit_file", "apply_patch"):
        path = (
            data.get("path")
            or data.get("file_path")
            or data.get("TargetFile")
            or data.get("filepath")
            or "file"
        )
        collapsed_box, full_box = _render_edit_card(
            path=str(path), diff_lines=raw_lines, success=success, elapsed_s=elapsed_s
        )
        return {"ui_summary": None, "ui_details": collapsed_box, "ui_details_full": full_box}

    # 3. Write -> Boxed file write card
    if name_lower in ("write", "write_file"):
        path = data.get("path") or data.get("file_path") or data.get("TargetFile") or "file"
        content = data.get("content") or data.get("CodeContent") or result
        content_lines = content.splitlines() if isinstance(content, str) else raw_lines
        collapsed_box, full_box = _render_write_card(
            path=str(path), content_lines=content_lines, success=success, elapsed_s=elapsed_s
        )
        return {"ui_summary": None, "ui_details": collapsed_box, "ui_details_full": full_box}

    # 4. Find / List Dir / LS -> Boxless output tree
    if name_lower in ("find", "find_files", "list_dir", "ls"):
        path = data.get("path") or data.get("SearchDirectory") or data.get("dir_path") or "."
        pattern = data.get("pattern") or data.get("Pattern")
        count = len(raw_lines)
        if pattern:
            header = f"Q Glob: {pattern} {count} files · in {path}"
        else:
            header = f"Q List: {count} files · in {path}"
        collapsed_tree, full_tree = _render_file_tree(header, raw_lines)
        return {"ui_summary": None, "ui_details": collapsed_tree, "ui_details_full": full_tree}

    # 5. Grep -> Boxless grep tree
    if name_lower == "grep":
        query = data.get("query") or data.get("Query") or data.get("pattern") or ""
        path = data.get("path") or data.get("SearchPath") or "."
        count = len(raw_lines)
        header = f"Q Grep: '{query}' {count} matches · in {path}"
        collapsed_tree, full_tree = _render_file_tree(header, raw_lines)
        return {"ui_summary": None, "ui_details": collapsed_tree, "ui_details_full": full_tree}

    # 6. Web / Web Search -> Boxless tree
    if name_lower in ("web", "web_search"):
        query = data.get("query") or data.get("q") or ""
        elapsed_str = f" · {elapsed_s:.2f}s" if elapsed_s is not None else ""
        header = f"🌐 Web: {query} ({len(raw_lines)} results){elapsed_str}"
        body = render_tree(raw_lines, max_lines=6)
        full = "\n".join(raw_lines)
        return {
            "ui_summary": header,
            "ui_details": body if body else None,
            "ui_details_full": full,
        }

    # 7. Read / Read file -> Quiet tool representation
    if name_lower in ("read", "read_file"):
        path = data.get("path") or data.get("file_path") or data.get("AbsolutePath") or ""
        offset = data.get("offset") or data.get("StartLine")
        limit = data.get("limit") or data.get("EndLine")
        range_str = f":{offset}-{limit}" if (offset or limit) else ""
        elapsed_str = f" · {elapsed_s:.2f}s" if elapsed_s is not None else ""
        summary = f"● Read {path}{range_str}{elapsed_str}"
        body = render_tree(raw_lines, max_lines=6)
        full = "\n".join(raw_lines)
        return {
            "ui_summary": summary,
            "ui_details": body if body else None,
            "ui_details_full": full,
        }

    # 8. Skill
    if name_lower == "skill":
        name_val = data.get("name") or data.get("skill_name") or ""
        elapsed_str = f" · {elapsed_s:.2f}s" if elapsed_s is not None else ""
        summary = f"λ Skill · {name_val}{elapsed_str}"
        body = render_tree(raw_lines, max_lines=6)
        full = "\n".join(raw_lines)
        return {
            "ui_summary": summary,
            "ui_details": body if body else None,
            "ui_details_full": full,
        }

    # 9. Task / Long task / Goal
    if name_lower in ("task", "long_task", "goal", "complete_goal"):
        goal_text = (
            data.get("goal")
            or data.get("objective")
            or data.get("recap")
            or data.get("Prompt")
            or raw_lines[0]
        )
        icon = "◈" if "task" in name_lower else "◉"
        elapsed_str = f" · {elapsed_s:.2f}s" if elapsed_s is not None else ""
        summary = f"{icon} {_trunc(goal_text, 80)}{elapsed_str}"
        body = render_tree(raw_lines, max_lines=6)
        full = "\n".join(raw_lines)
        return {
            "ui_summary": summary,
            "ui_details": body if body else None,
            "ui_details_full": full,
        }

    # 10. Cron / Message / Stdin / Exec sessions
    if name_lower in ("cron", "message", "write_stdin", "list_exec_sessions"):
        icon = tool_icon(name_lower)
        call_info = format_call(name_lower, data)
        elapsed_str = f" · {elapsed_s:.2f}s" if elapsed_s is not None else ""
        summary = (
            f"{icon} {name_lower.capitalize()}: {call_info}{elapsed_str}"
            if call_info
            else f"{icon} {name_lower.capitalize()}{elapsed_str}"
        )
        body = render_tree(raw_lines, max_lines=6)
        full = "\n".join(raw_lines)
        return {
            "ui_summary": summary,
            "ui_details": body if body else None,
            "ui_details_full": full,
        }

    # Standard / Fallback tree parsing
    first = raw_lines[0].strip()
    if not success:
        first = f"✗ {first}" if not first.startswith("Error") else first
    summary = _trunc(first)

    if len(raw_lines) == 1:
        return {"ui_summary": summary, "ui_details": None, "ui_details_full": None}

    metrics_bits: list[str] = []
    if elapsed_s is not None:
        metrics_bits.append(f"⏱ {elapsed_s:.2f}s")
    metrics_bits.append(f"{len(raw_lines)} lines")
    metrics = "  ╰─ " + " · ".join(metrics_bits)

    body = render_tree(raw_lines[1:])
    details = f"{body}\n{metrics}" if body else metrics
    full = "\n".join(raw_lines[1:])
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
