"""Goal dashboard UI: the above-editor beacon widget plus renderers.

Two modes share one presentation model, so they can never disagree:

- compact: always visible above the editor while a goal is focused
- expanded: full task tree + verification + recent activity, rendered
  into the chat log via Ctrl+Shift+G

Render functions are pure (data in, Rich Text out), matching the
convention in :mod:`vtx.tui.task_ui`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.style import Style
from rich.text import Text
from textual.widgets import Static

from vtx.coding_agent.config import config
from vtx.coding_agent.goal.record import GoalRecord, count_tasks, current_task, objective_title
from vtx.coding_agent.goal.service import GoalService, get_service

if TYPE_CHECKING:
    from vtx.coding_agent.config import ColorsConfig

# Task markers: ✓ complete · ▸ current · ~ skipped · · pending
TASK_MARKS = {"complete": "✓", "current": "▸", "skipped": "~", "pending": "·"}


def _colors() -> ColorsConfig:
    return config.ui.colors


def progress_bar(pct: int, width: int = 8) -> str:
    width = max(4, min(width, 24))
    filled = round(pct * width / 100)
    return "█" * filled + "░" * (width - filled)


def format_usage(record: GoalRecord) -> str:
    total_ms = record.usage.elapsed_ms
    tokens = record.usage.total_tokens()
    if total_ms <= 0 and tokens <= 0:
        return ""
    minutes, seconds = divmod(int(total_ms // 1000), 60)
    hours, minutes = divmod(minutes, 60)
    elapsed = f"{hours}h{minutes:02d}m" if hours else f"{minutes}m{seconds:02d}s"
    token_text = f"{tokens / 1000:.1f}K" if tokens >= 1000 else str(tokens)
    budget = ""
    if record.token_budget:
        budget = f"/{record.token_budget // 1000}K"
    return f"{elapsed} {token_text}{budget}"


def task_mark(record: GoalRecord, task_id: str) -> str:
    task = next((t for t in record.tasks if t.id == task_id), None)
    if task is None:
        return TASK_MARKS["pending"]
    if task.status == "complete":
        return TASK_MARKS["complete"]
    if task.status == "skipped":
        return TASK_MARKS["skipped"]
    active = current_task(record)
    if active is not None and active.id == task.id:
        return TASK_MARKS["current"]
    return TASK_MARKS["pending"]


def mark_color(mark: str) -> str:
    colors = _colors()
    return {
        TASK_MARKS["complete"]: colors.success,
        TASK_MARKS["current"]: colors.accent,
        TASK_MARKS["skipped"]: colors.dim,
        TASK_MARKS["pending"]: colors.notice,
    }.get(mark, colors.fg)


def status_style(status: str) -> str:
    colors = _colors()
    styles: dict[str, str] = {
        "active": colors.success,
        "paused": colors.notice,
        "blocked": colors.failed,
        "budget_limited": colors.notice,
        "complete": colors.success,
    }
    return styles.get(status, colors.fg)


# ---------------------------------------------------------------------------
# Compact widget
# ---------------------------------------------------------------------------


def render_compact(
    service: GoalService, record: GoalRecord, *, expanded_hint: str = "ctrl+shift+g"
) -> Text:
    """The above-editor beacon: header, status, task window, current task."""
    c = _colors()
    done, total = count_tasks(record.tasks)
    pct = round(done * 100 / total) if total else 0

    title = objective_title(record.objective, max_chars=48)
    open_extra = len(service.pool()) - 1

    text = Text()
    # Header: ◈ vtx-goal: <objective> ── [████████░░] 75% (3/4)
    text.append("◈ ", style=Style(color=c.accent, bold=True))
    text.append("vtx-goal", style=Style(color=c.accent, bold=True))
    text.append(f" ─ {title} ", style=Style(color=c.title, bold=True))

    if total > 0:
        bar = progress_bar(pct, width=10)
        text.append(f"── [{bar}] {pct}% ({done}/{total})", style=Style(color=c.accent))
    text.append("\n")

    # Status / subtask line
    status_text = Text()
    status_text.append("goal: ", style=Style(color=c.dim))
    status_text.append(record.label(), style=Style(color=status_style(record.status), bold=True))
    usage = format_usage(record)
    if usage:
        status_text.append(f" [{usage}]", style=Style(color=c.dim))
    if open_extra > 0:
        status_text.append(f" (+{open_extra} open)", style=Style(color=c.muted))
    text.append(status_text)
    text.append("\n\n")

    if total > 0:
        for line in _task_lines_window(record):
            if not line:
                continue
            mark = line[0]
            if mark in TASK_MARKS.values():
                text.append(mark, style=Style(color=mark_color(mark), bold=True))
                text.append(line[1:], style=Style(color=c.fg))
            else:
                text.append(line, style=Style(color=c.dim, italic=True))
            text.append("\n")
        text.append("\n")

    active = current_task(record)
    active_val = (
        f"{active.id} · {objective_title(active.title, 36)}"
        if active
        else ("all tasks complete" if done == total else "none")
    )
    verify_val = (
        objective_title(record.verification.strip(), 36) if record.verification.strip() else "none"
    )
    file_val = _file_label(service.cwd, record.id)

    footer_row1 = Text()
    footer_row1.append("Current ", style=Style(color=c.dim))
    footer_row1.append(active_val.ljust(40), style=Style(color=c.fg))
    footer_row1.append(" Verify  ", style=Style(color=c.dim))
    footer_row1.append(verify_val, style=Style(color=c.fg))
    text.append(footer_row1)
    text.append("\n")

    footer_row2 = Text()
    footer_row2.append("File    ", style=Style(color=c.dim))
    footer_row2.append(file_val.ljust(40), style=Style(color=c.muted))
    footer_row2.append(" Hints   ", style=Style(color=c.dim))
    footer_row2.append(f"Esc: pause · {expanded_hint}: expand", style=Style(color=c.dim))
    text.append(footer_row2)

    return text


def _subtask_progress(record: GoalRecord) -> tuple[int, int]:
    parent = current_task(record)
    if parent is None:
        return 0, 0
    subs = [t for t in record.tasks if t.parent_id == parent.id]
    if not subs:
        return 0, 0
    return sum(1 for t in subs if t.status == "complete"), len(subs)


def _task_lines_window(record: GoalRecord, limit: int = 5) -> list[str]:
    """Scrollable task window anchored to the most recently completed task."""
    top = [t for t in record.tasks if not t.parent_id]
    lines: list[str] = []
    active = current_task(record)
    last_done = max((i for i, t in enumerate(top) if t.status == "complete"), default=-1)
    start = max(0, last_done - (limit - 2)) if len(top) > limit else 0
    window = top[start : start + limit]

    def fmt(task, indent: bool) -> str:
        mark = task_mark(record, task.id)
        prefix = "  " if indent else ""
        return f"{prefix}{mark} {task.id}  {task.title}"

    for task in window:
        lines.append(fmt(task, indent=False))
        subs = [t for t in record.tasks if t.parent_id == task.id]
        if subs and (active is not None and (active.id == task.id or active.parent_id == task.id)):
            sub_active = current_task(record)
            for sub in subs[:3]:
                mark = (
                    TASK_MARKS["current"]
                    if sub_active is not None and sub.id == sub_active.id
                    else task_mark(record, sub.id)
                )
                lines.append(f"    {mark} {sub.id}  {objective_title(sub.title, 40)}")
    if start > 0:
        lines.insert(0, "… earlier tasks hidden")
    remaining = len(top) - (start + len(window))
    if remaining > 0:
        lines.append(f"… +{remaining} more task{'s' if remaining != 1 else ''}")
    return lines


def _file_label(cwd: str, goal_id: str) -> str:
    from vtx.coding_agent.goal.storage import find_goal_file

    path = find_goal_file(cwd, goal_id)
    if path is None:
        return ".vtx/goals/"
    try:
        rel = path.relative_to(cwd)
    except ValueError:
        return str(path)
    text = str(rel)
    if len(text) > 36:
        return "…" + text[-35:]
    return text


def _content_width(text: Text) -> int:
    return max((len(line.plain) for line in text.split("\n")), default=0)


class GoalWidget(Static):
    """Above-editor goal beacon. Hidden when no goal is focused."""

    DEFAULT_CSS = """
    GoalWidget {
        display: none;
        padding: 1 2;
        margin: 0 1 1 1;
        border: round $primary;
        background: $panel;
        height: auto;
    }
    GoalWidget.-visible {
        display: block;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._cwd = ""

    def set_cwd(self, cwd: str) -> None:
        self._cwd = cwd

    def refresh_goal(self, cwd: str | None = None) -> None:
        """Re-render from disk state; hide when nothing is focused."""
        cwd = cwd or self._cwd
        if not cwd:
            self.remove_class("-visible")
            return
        service = get_service(cwd)
        record = service.focused() if not service.settings.get("disabled") else None
        if record is None:
            self.remove_class("-visible")
            self.renderable = Text("")
            return
        self.add_class("-visible")
        self.update(render_compact(service, record))


# ---------------------------------------------------------------------------
# Expanded dashboard
# ---------------------------------------------------------------------------


def render_expanded(service: GoalService, record: GoalRecord, *, activity_limit: int = 6) -> Text:
    """Full unified dashboard: progress, task tree, contracts, activity."""
    from vtx.coding_agent.goal.storage import recent_activity

    c = _colors()
    done, total = count_tasks(record.tasks)
    pct = round(done * 100 / total) if total else 0

    text = Text()
    text.append("vtx-goal", style=Style(color=c.accent, bold=True))
    text.append(" ─ ", style=Style(color=c.dim))
    text.append(objective_title(record.objective, 70), style=Style(color=c.title))
    text.append("\n")
    text.append("Status ", style=Style(color=c.dim))
    text.append(record.label(), style=Style(color=status_style(record.status)))
    usage = format_usage(record)
    text.append(
        f" · [{usage}] · id {record.id} · mode {record.mode}"
        if usage
        else f" · id {record.id} · mode {record.mode}",
        style=Style(color=c.dim),
    )

    # Progress
    text.append(f"\n\n{'─' * 3} Progress ", style=Style(color=c.border))
    text.append(
        f"\n[{progress_bar(pct, 12)}] {done}/{top_level_total(record)} tasks · {pct}%",
        style=Style(color=c.accent),
    )

    # Tasks
    text.append(f"\n\n{'─' * 3} Tasks ", style=Style(color=c.border))
    if record.tasks:
        text.append("\n")
        text.append(_task_tree_text(record))
    else:
        text.append("\n(no task plan)", style=Style(color=c.dim))

    # Current task block
    active = current_task(record)
    text.append(f"\n{'─' * 3} Current task ", style=Style(color=c.border))
    if active is not None:
        subs = [t for t in record.tasks if t.parent_id == active.id]
        text.append(f"\n[{active.id}] {active.title}", style=Style(color=c.fg))
        if subs:
            sub_done = sum(1 for t in subs if t.status == "complete")
            spct = round(sub_done * 100 / len(subs))
            text.append(
                f"\nSubtasks [{progress_bar(spct)}] {sub_done}/{len(subs)} · {spct}%",
                style=Style(color=c.accent),
            )
        contract = _task_contract(active)
        if contract:
            text.append("\nContract: ", style=Style(color=c.dim))
            text.append(contract, style=Style(color=c.notice))
        if active.evidence:
            text.append("\nEvidence: ", style=Style(color=c.dim))
            text.append(objective_title(active.evidence, 90), style=Style(color=c.success))
    else:
        text.append(
            "\n(none — all tasks complete)" if total else "\n(none)", style=Style(color=c.dim)
        )

    # Goal verification
    text.append(f"\n\n{'─' * 3} Verification ", style=Style(color=c.border))
    if record.verification.strip():
        text.append(f"\n{record.verification.strip()}", style=Style(color=c.notice))
    else:
        text.append(
            "\n(no contract — auditor judges against the objective)", style=Style(color=c.dim)
        )

    if record.review_feedback:
        text.append(f"\n\n{'─' * 3} Auditor feedback ", style=Style(color=c.failed))
        text.append(f"\n{record.review_feedback.strip()}", style=Style(color=c.failed))

    # Recent activity
    activity = recent_activity(service.cwd, record.id, limit=activity_limit)
    text.append(f"\n\n{'─' * 3} Activity ", style=Style(color=c.border))
    if activity:
        for line in activity:
            text.append(f"\n{line}", style=Style(color=c.dim))
    else:
        text.append("\n(no recorded activity yet)", style=Style(color=c.dim))

    file_path = _file_label(service.cwd, record.id)
    text.append(f"\n\nFile: {file_path}", style=Style(color=c.muted))
    return text


def top_level_total(record: GoalRecord) -> int:
    return sum(1 for t in record.tasks if not t.parent_id)


def _task_contract(task) -> str:
    note = task.note.strip()
    if note.lower().startswith("contract:"):
        return note[len("contract:") :].strip()
    return ""


def _task_tree_text(record: GoalRecord) -> Text:
    c = _colors()
    active = current_task(record)
    out = Text()

    def emit(task, depth: int) -> None:
        mark = task_mark(record, task.id)
        is_current = active is not None and active.id == task.id
        style = Style(color=c.accent, bold=is_current)
        out.append("  " * depth)
        out.append(mark + " ", style=Style(color=mark_color(mark)))
        out.append(task.id.ljust(5), style=style)
        out.append(task.title, style=style)
        if task.status == "complete" and task.evidence:
            out.append(f"  — {objective_title(task.evidence, 50)}", style=Style(color=c.success))
        elif task.status == "skipped" and task.note:
            out.append(f"  — skipped: {objective_title(task.note, 40)}", style=Style(color=c.dim))
        out.append("\n")

    for task in [t for t in record.tasks if not t.parent_id]:
        emit(task, 0)
        for sub in [t for t in record.tasks if t.parent_id == task.id]:
            emit(sub, 1)
    return out
