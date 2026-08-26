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


def progress_bar_text(pct: int, width: int = 8) -> Text:
    """Theme-aware two-tone bar: filled segment in accent/success, track in border."""
    width = max(4, min(width, 24))
    filled = round(pct * width / 100)
    c = _colors()
    out = Text()
    out.append("█" * filled, style=Style(color=c.accent if pct < 100 else c.success, bold=True))
    out.append("░" * (width - filled), style=Style(color=c.border))
    return out


def status_dot(status: str) -> Text:
    """Colored ● indicating goal status, driven by the active theme."""
    return Text("● ", style=Style(color=status_style(status), bold=True))


def title_chip(label: str = "vtx-goal") -> Text:
    """Badge-style label chip using the theme's badge colors."""
    c = _colors()
    return Text(f" {label} ", style=Style(color=c.badge.label, bgcolor=c.badge.bg, bold=True))


def section_header(title: str, color: str | None = None) -> Text:
    """Modern `◆ Title` section marker for the expanded dashboard."""
    c = _colors()
    out = Text()
    out.append("◆ ", style=Style(color=color or c.accent, bold=True))
    out.append(title, style=Style(color=color or c.title, bold=True))
    return out


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

    title = objective_title(record.objective, max_chars=52)
    open_extra = len(service.pool()) - 1

    text = Text()
    # Header row: ╭─ [vtx-goal] ─ <objective>
    text.append("╭─ ", style=Style(color=c.border))
    text.append(title_chip())
    text.append(" ─ ", style=Style(color=c.border))
    text.append(title, style=Style(color=c.title))
    text.append(" ╮\n", style=Style(color=c.border))

    # Status row: ● running [12m47s 18.2K] (+2 open)
    status_text = Text()
    status_text.append(status_dot(record.status))
    status_text.append(record.label(), style=Style(color=status_style(record.status)))
    usage = format_usage(record)
    if usage:
        status_text.append(f" [{usage}]", style=Style(color=c.dim))
    if open_extra > 0:
        status_text.append(f"  +{open_extra} open", style=Style(color=c.muted))
    text.append(Text("│ ", style=Style(color=c.border)))
    text.append(status_text)
    text.append("\n", style=Style(color=c.border))

    active = current_task(record)

    if total > 0:
        # Tasks header with two-tone progress bar
        sub_done, sub_total = _subtask_progress(record)
        header = Text()
        header.append("Tasks ", style=Style(color=c.dim))
        header.append(f"✓{done}/{total}", style=Style(color=c.success))
        header.append("  ", style=Style(color=c.dim))
        header.append(progress_bar_text(pct))
        header.append(f" {pct}%", style=Style(color=c.accent, bold=True))
        if sub_total > 0:
            header.append(f"  · sub {sub_done}/{sub_total}", style=Style(color=c.dim))
        text.append(Text("├─ ", style=Style(color=c.border)))
        text.append(header)
        text.append(" ┤\n", style=Style(color=c.border))

        for line in _task_lines_window(record):
            mark = line[0]
            is_current = active is not None and f"{active.id}" in line
            body = Text()
            body.append(mark, style=Style(color=mark_color(mark), bold=is_current))
            body.append(
                line[1:], style=Style(color=c.accent if is_current else c.fg, bold=is_current)
            )
            text.append(Text("│ ", style=Style(color=c.border)))
            text.append(body)
            text.append("\n", style=Style(color=c.border))

    rows: list[tuple[str, str]] = []
    if active is not None:
        rows.append(("Current", f"{active.id} · {objective_title(active.title, 46)}"))
    elif total > 0 and done == total:
        rows.append(("Current", "all tasks complete"))
    if record.verification.strip():
        rows.append(("Verify", objective_title(record.verification.strip(), 48)))
    rows.append(("File", _file_label(service.cwd, record.id)))

    label_width = max(len(label) for label, _ in rows)
    for label, value in rows:
        text.append("│ ", style=Style(color=c.border))
        text.append(label.ljust(label_width), style=Style(color=c.dim))
        text.append("  ", style=Style(color=c.dim))
        value_color = c.fg if label != "File" else c.muted
        text.append(value, style=Style(color=value_color))
        text.append("\n", style=Style(color=c.border))

    footer = f"Esc: pause goal   {expanded_hint}: expand tasks"
    pad = max(0, _content_width(text) - len(footer) - 2)
    text.append("╰─ ", style=Style(color=c.border))
    text.append(footer, style=Style(color=c.dim))
    text.append(" " * pad + "╯", style=Style(color=c.border))
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
        flag = ""
        if task.status == "complete" and task.evidence:
            flag = " ☑"
        return f"{prefix}{mark} {task.id}  {task.title}{flag}"

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
    if len(text) > 52:
        return "…" + text[-51:]
    return text


def _content_width(text: Text) -> int:
    return max((len(line.plain) for line in text.split("\n")), default=0)


class GoalWidget(Static):
    """Above-editor goal beacon. Hidden when no goal is focused."""

    DEFAULT_CSS = """
    GoalWidget {
        display: none;
        padding: 0 1;
        margin: 0 1;
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
    text.append(title_chip())
    text.append(" ─ ", style=Style(color=c.dim))
    text.append(objective_title(record.objective, 70), style=Style(color=c.title))
    text.append("\n")
    text.append(status_dot(record.status))
    text.append(record.label(), style=Style(color=status_style(record.status)))
    usage = format_usage(record)
    meta = Text()
    meta.append("  ", style=Style(color=c.dim))
    if usage:
        meta.append(f"[{usage}] · ", style=Style(color=c.dim))
    meta.append(f"id {record.id} · mode {record.mode}", style=Style(color=c.muted))
    text.append(meta)

    # Progress
    text.append("\n\n")
    text.append(section_header("Progress"))
    bar_row = Text("\n")
    bar_row.append(progress_bar_text(pct, 12))
    bar_row.append(f"  {done}/{top_level_total(record)} tasks", style=Style(color=c.fg))
    bar_row.append(f"  {pct}%", style=Style(color=c.accent, bold=True))
    text.append(bar_row)

    # Tasks
    text.append("\n\n")
    text.append(section_header("Tasks"))
    if record.tasks:
        text.append("\n")
        text.append(_task_tree_text(record))
    else:
        text.append("\n(no task plan)", style=Style(color=c.dim))

    # Current task block
    active = current_task(record)
    text.append("\n\n")
    text.append(section_header("Current task"))
    if active is not None:
        subs = [t for t in record.tasks if t.parent_id == active.id]
        text.append(f"\n[{active.id}] ", style=Style(color=c.accent, bold=True))
        text.append(active.title, style=Style(color=c.fg))
        if subs:
            sub_done = sum(1 for t in subs if t.status == "complete")
            spct = round(sub_done * 100 / len(subs))
            text.append("\nSubtasks ", style=Style(color=c.dim))
            text.append(progress_bar_text(spct))
            text.append(f" {sub_done}/{len(subs)} · {spct}%", style=Style(color=c.accent))
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
    text.append("\n\n")
    text.append(section_header("Verification", color=c.notice))
    if record.verification.strip():
        text.append(f"\n{record.verification.strip()}", style=Style(color=c.notice))
    else:
        text.append(
            "\n(no contract — auditor judges against the objective)", style=Style(color=c.dim)
        )

    if record.review_feedback:
        text.append("\n\n")
        text.append(section_header("Auditor feedback", color=c.failed))
        text.append(f"\n{record.review_feedback.strip()}", style=Style(color=c.failed))

    # Recent activity
    activity = recent_activity(service.cwd, record.id, limit=activity_limit)
    text.append("\n\n")
    text.append(section_header("Activity"))
    if activity:
        for i, line in enumerate(activity):
            connector = "╰ " if i == len(activity) - 1 else "├ "
            text.append("\n" + connector, style=Style(color=c.border))
            text.append(line, style=Style(color=c.dim))
    else:
        text.append("\n(no recorded activity yet)", style=Style(color=c.dim))

    file_path = _file_label(service.cwd, record.id)
    text.append(Text("\nFile: ", style=Style(color=c.muted)))
    text.append(file_path, style=Style(color=c.border))
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
    top = [t for t in record.tasks if not t.parent_id]
    out = Text()

    def emit(task, depth: int, last: bool) -> None:
        mark = task_mark(record, task.id)
        is_current = active is not None and active.id == task.id
        style = Style(color=c.accent if is_current else c.fg, bold=is_current)
        if depth > 0:
            out.append("└ " if last else "├ ", style=Style(color=c.border))
        out.append(mark + " ", style=Style(color=mark_color(mark), bold=is_current))
        out.append(task.id.ljust(5), style=style)
        out.append(task.title, style=style)
        if task.status == "complete" and task.evidence:
            out.append(f"  — {objective_title(task.evidence, 50)}", style=Style(color=c.success))
        elif task.status == "skipped" and task.note:
            out.append(f"  — skipped: {objective_title(task.note, 40)}", style=Style(color=c.dim))
        out.append("\n")

    for task in top:
        subs = [t for t in record.tasks if t.parent_id == task.id]
        emit(task, 0, last=False)
        for j, sub in enumerate(subs):
            emit(sub, 1, last=j == len(subs) - 1)
    return out
