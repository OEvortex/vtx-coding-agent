"""Goal persistence: markdown goal files plus an append-only JSONL ledger."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from .record import GoalRecord, utc_now_iso

log = logging.getLogger("agent.goal")

META_BEGIN = "<!-- vtx-goal:v1"
META_END = "-->"

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def goals_dir(cwd: str) -> Path:
    path = Path(cwd) / ".vtx" / "goals"
    path.mkdir(parents=True, exist_ok=True)
    return path


def archived_dir(cwd: str) -> Path:
    path = goals_dir(cwd) / "archived"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ledger_path(cwd: str) -> Path:
    return goals_dir(cwd) / "ledger.jsonl"


def settings_path(cwd: str) -> Path:
    return goals_dir(cwd) / "settings.json"


def _check_component(value: str) -> str:
    if not value or not _SAFE_ID_RE.match(value):
        raise ValueError(f"Unsafe goal path component: {value!r}")
    return value


def goal_filename(record: GoalRecord, now: str | None = None) -> str:
    stamp = (now or record.created_at or utc_now_iso())[:19]
    stamp = re.sub(r"[:]", "", stamp).replace("-", "").replace("T", "-")
    return f"active_goal_{stamp}_{_check_component(record.id)}.md"


def find_goal_file(cwd: str, goal_id: str) -> Path | None:
    """Locate a goal file by id across active and archived directories."""
    _check_component(goal_id)
    for directory in (goals_dir(cwd), archived_dir(cwd)):
        try:
            candidates = sorted(directory.glob(f"*_{goal_id}.md"))
        except OSError:
            continue
        if candidates:
            return candidates[0]
    return None


def serialize(record: GoalRecord) -> str:
    meta = dict(record.to_meta())
    meta["objective"] = record.objective
    body_parts = [
        META_BEGIN,
        json.dumps(meta, ensure_ascii=False, indent=1),
        META_END,
        "",
        "# Objective",
        "",
        record.objective.strip(),
        "",
    ]
    if record.verification.strip():
        body_parts += ["# Verification", "", record.verification.strip(), ""]
    if record.prompt.strip():
        body_parts += ["# Goal Prompt", "", record.prompt.strip(), ""]
    if record.tasks:
        body_parts += ["# Tasks", "", render_tasks_markdown(record), ""]
    if record.review_feedback:
        body_parts += ["# Review Feedback", "", record.review_feedback.strip(), ""]
    return "\n".join(body_parts)


def render_tasks_markdown(record: GoalRecord) -> str:
    lines: list[str] = []
    for task in record.tasks:
        depth = 0
        parent = task.parent_id
        while parent is not None:
            depth += 1
            parent = next((t.parent_id for t in record.tasks if t.id == parent), None)
        mark = {"complete": "[x]", "skipped": "[-]"}.get(task.status, "[ ]")
        line = f"{'  ' * depth}- {mark} {task.id}  {task.title}"
        if task.status == "complete" and task.evidence:
            line += f"  — {task.evidence}"
        elif task.status == "skipped" and task.note:
            line += f"  — skipped: {task.note}"
        lines.append(line)
    return "\n".join(lines)


def parse(text: str) -> GoalRecord | None:
    """Parse a goal markdown file. Returns None for malformed content."""
    begin = text.find(META_BEGIN)
    if begin < 0:
        return None
    json_start = text.find("\n", begin) + 1
    end = text.find(META_END, json_start)
    if end < 0:
        return None
    try:
        meta = json.loads(text[json_start:end].strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(meta, dict) or not meta.get("id"):
        return None

    objective_body = _extract_section(text, "Objective")
    prompt_body = _extract_section(text, "Goal Prompt")
    verification_body = _extract_section(text, "Verification")

    record = GoalRecord.from_meta(meta, prompt_body=prompt_body, objective_body=objective_body)
    if verification_body and not record.verification:
        record.verification = verification_body
    return record


def _extract_section(text: str, heading: str) -> str:
    match = re.search(rf"^#\s+{re.escape(heading)}\s*$", text, re.MULTILINE)
    if not match:
        return ""
    rest = text[match.end() :]
    next_heading = re.search(r"^#\s+\S.*$", rest, re.MULTILINE)
    if next_heading:
        rest = rest[: next_heading.start()]
    return rest.strip()


def read_active_pool(cwd: str) -> dict[str, GoalRecord]:
    """Scan the goals directory and return open records keyed by id."""
    pool: dict[str, GoalRecord] = {}
    directory = goals_dir(cwd)
    try:
        entries = sorted(
            p
            for p in directory.iterdir()
            if p.is_file() and p.name.startswith("active_goal_") and p.name.endswith(".md")
        )
    except OSError:
        return pool
    for path in entries:
        if path.is_symlink():
            continue
        try:
            record = parse(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if record is None or not record.is_open():
            continue
        pool[record.id] = record
    return pool


def write_active(cwd: str, record: GoalRecord) -> Path:
    path = goals_dir(cwd) / goal_filename(record)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(serialize(record), encoding="utf-8")
    os.replace(tmp, path)
    return path


def archive(cwd: str, record: GoalRecord) -> Path:
    source = find_goal_file(cwd, record.id)
    dest = archived_dir(cwd) / (source.name if source else goal_filename(record))
    payload = serialize(record)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, dest)
    if source is not None and source.resolve() != dest.resolve():
        try:
            source.unlink()
        except OSError:
            log.warning("goal %s: could not remove active copy %s", record.id, source)
    return dest


def append_ledger(cwd: str, event_type: str, goal_id: str | None = None, **detail) -> bool:
    """Best-effort ledger append. Returns False when the write failed."""
    entry = {"ts": utc_now_iso(), "type": event_type}
    if goal_id:
        entry["goal_id"] = goal_id
    entry.update(detail)
    try:
        with ledger_path(cwd).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        log.warning("goal ledger append failed: %s", exc)
        return False


def read_ledger(cwd: str, *, limit_last: int = 50, goal_id: str | None = None) -> list[dict]:
    path = ledger_path(cwd)
    if not path.exists():
        return []
    events: list[dict] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if goal_id and event.get("goal_id") != goal_id:
                    continue
                events.append(event)
    except OSError:
        return events
    return events[-limit_last:]


ACTIVITY_VERBS: dict[str, str] = {
    "created": "created",
    "status_changed": "status →",
    "tweaked": "tweaked",
    "task_list_set": "task list set",
    "task_updated": "task",
    "focus_changed": "focused",
    "completed": "completed",
    "archived": "archived",
    "audit_approved": "audit approved",
    "audit_changes_required": "audit changes required",
    "audit_skipped": "audit skipped",
    "budget_limited": "budget reached",
}


def recent_activity(cwd: str, goal_id: str | None = None, limit: int = 8) -> list[str]:
    """Human-readable one-line summaries of the latest ledger events."""
    out: list[str] = []
    for event in reversed(read_ledger(cwd, limit_last=200, goal_id=goal_id)):
        etype = str(event.get("type") or "")
        ts = str(event.get("ts") or "")[:19].replace("T", " ")
        verb = ACTIVITY_VERBS.get(etype, etype)
        detail = ""
        if etype == "task_updated":
            detail = f"{event.get('task_id')} → {event.get('status')}"
            if event.get("title"):
                detail += f" · {event['title']}"
        elif etype == "status_changed":
            detail = str(event.get("status") or "")
        elif etype in ("audit_approved", "audit_changes_required"):
            detail = str(event.get("summary") or "")[:80]
        elif etype == "tweaked":
            detail = str(event.get("change") or "")[:80]
        line = f"{ts} · {verb}" + (f" {detail}" if detail else "")
        out.append(line[:140])
        if len(out) >= limit:
            break
    return out


DEFAULT_SETTINGS: dict = {
    "disabled": False,
    "autoContinue": True,
    "disableTasks": False,
    "disableContracts": False,
    "auditorEnabled": True,
    "autoSelectSingleGoal": True,
}


def load_settings(cwd: str) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    path = settings_path(cwd)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings
    if isinstance(data, dict):
        for key in DEFAULT_SETTINGS:
            if key in data and isinstance(data[key], bool):
                settings[key] = data[key]
    return settings


def save_settings(cwd: str, settings: dict) -> None:
    clean = {k: settings.get(k, DEFAULT_SETTINGS[k]) for k in DEFAULT_SETTINGS}
    tmp = settings_path(cwd).with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, settings_path(cwd))


def format_file_timestamp(name: str) -> str:
    """Extract a compact timestamp from ``active_goal_20260826-100359_ab12.md``."""
    match = re.search(r"active_goal_(\d{8})-(\d{6})_", name)
    if not match:
        return ""
    try:
        dt = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


__all__ = [
    "ACTIVITY_VERBS",
    "DEFAULT_SETTINGS",
    "META_BEGIN",
    "META_END",
    "append_ledger",
    "archive",
    "archived_dir",
    "find_goal_file",
    "format_file_timestamp",
    "goal_filename",
    "goals_dir",
    "ledger_path",
    "load_settings",
    "parse",
    "read_active_pool",
    "read_ledger",
    "recent_activity",
    "render_tasks_markdown",
    "save_settings",
    "serialize",
    "settings_path",
    "write_active",
]
