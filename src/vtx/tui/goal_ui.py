"""Goal dashboard UI: the above-editor beacon widget plus renderers.

Two modes share one presentation model, so they can never disagree:

- compact: always visible above the editor while a goal is focused
- expanded: full task tree + verification + recent activity, rendered
  into the chat log via Ctrl+Shift+G

Render functions are pure (data in, Rich Text out), matching the
convention in :mod:`vtx.tui.task_ui`.

Layout rules the whole module follows:

- every width is measured in terminal *cells* (``cell_len``), never
  characters, so CJK/emoji objectives cannot shear the box borders
- the compact renderer is handed the real widget width and adapts; nothing
  is hardcoded to a nominal 80 columns
- one-line fields ellipsize on a word boundary, multi-line fields wrap
- full text is always one keypress away (the expanded dashboard) and always
  present verbatim in the goal file
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.console import Console
from rich.style import Style
from rich.text import Text
from textual.widgets import Static

from vtx.ai.agent.goal.record import GoalRecord, count_tasks, current_task, truncate_on_words
from vtx.ai.agent.goal.service import GoalService, get_service
from vtx.ai.config import config
from vtx.tui.goal_agents import REGISTRY, SubagentRun

if TYPE_CHECKING:
    from vtx.tui.themes import ColorsConfig

# Task markers: ✓ complete · ▸ current · ~ skipped · · pending
TASK_MARKS = {"complete": "✓", "current": "▸", "skipped": "~", "pending": "·"}

#: Width the compact beacon falls back to before the widget has been laid out
#: (and in tests that never mount the app).
FALLBACK_WIDTH = 80

#: Below this the box chrome costs more than it communicates, so the beacon
#: degrades to a single status line instead of drawing a box.
MIN_BOX_WIDTH = 44

#: Reused console for width maths; never printed to.
_WRAP_CONSOLE = Console(width=FALLBACK_WIDTH)


def _colors() -> ColorsConfig:
    return config.ui.colors


def format_tokens(count: int) -> str:
    """Compact token count: 940, 33.8K, 27.6M, 100K.

    Round magnitudes drop the decimal so a 100K budget reads `18.2K/100K`
    rather than `18.2K/100.0K`.
    """
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M".replace(".0M", "M")
    if count >= 1000:
        return f"{count / 1000:.1f}K".replace(".0K", "K")
    return str(count)


def format_usage(record: GoalRecord) -> str:
    """Elapsed time + tokens, e.g. `12m47s 18.2K/200K`.

    Long runs reach the millions, so the token figure scales to M — a goal
    that has burned 27M tokens should not read as `27631.9K`.
    """
    total_ms = record.usage.elapsed_ms
    tokens = record.usage.total_tokens()
    if total_ms <= 0 and tokens <= 0:
        return ""
    minutes, seconds = divmod(int(total_ms // 1000), 60)
    hours, minutes = divmod(minutes, 60)
    elapsed = f"{hours}h{minutes:02d}m" if hours else f"{minutes}m{seconds:02d}s"
    token_text = format_tokens(tokens)
    budget = ""
    if record.token_budget:
        budget = f"/{format_tokens(record.token_budget)}"
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
    filled = min(width, round(pct * width / 100))
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
# width helpers
# ---------------------------------------------------------------------------


def _content_width(text: Text) -> int:
    """Widest line of ``text`` in terminal cells."""
    return max((cell_len(line.plain) for line in text.split("\n")), default=0)


def _ellipsize(text: str, width: int) -> str:
    return truncate_on_words(" ".join((text or "").split()), max(0, width))


def _fit(content: Text, width: int) -> Text:
    """Force a single-line ``Text`` to exactly ``width`` cells (pad or clip)."""
    if width <= 0:
        return Text("")
    current = cell_len(content.plain)
    if current > width:
        clipped = Text(truncate_on_words(content.plain, width))
        clipped.spans = [s for s in content.spans if s.start < width]
        content = clipped
        current = cell_len(content.plain)
    if current < width:
        content.pad_right(width - current)
    return content


class _Box:
    """A bordered box whose rows are exactly ``width`` cells by construction.

    Every row is emitted as ``│ <inner> │`` and forced to ``inner`` cells, so
    an over-long label or a double-width CJK character can never shear the
    border. Doing this arithmetic by hand at each call site is what made the
    old beacon go ragged, so it lives in one place instead.
    """

    def __init__(self, width: int, border: str) -> None:
        self.width = max(8, width)
        self.border = border
        # 2 cells of "│ " on the left, 2 cells of " │" on the right.
        self.inner = self.width - 4

    def top(self) -> Text:
        line = Text("╭─", style=Style(color=self.border))
        line.append("─" * max(0, self.width - 4), style=Style(color=self.border))
        line.append("─╮", style=Style(color=self.border))
        return line

    def row(self, content: Text) -> Text:
        """One content row, forced to exactly ``inner`` cells."""
        line = Text("│ ", style=Style(color=self.border))
        line.append_text(_fit(content, self.inner))
        line.append(" │", style=Style(color=self.border))
        return line

    def rule_row(self, content: Text) -> Text:
        """A section divider that closes with `┤` rather than `│`.

        `├─` and `┤` are 2 and 1 cells, so the content gets one cell *more*
        than a normal row to land on the same total width.
        """
        line = Text("├─", style=Style(color=self.border))
        line.append_text(_fit(content, self.inner + 1))
        line.append("┤", style=Style(color=self.border))
        return line

    def footer(self, content: Text) -> Text:
        line = Text("╰─", style=Style(color=self.border))
        line.append_text(_fit(content, self.inner))
        line.append("─╯", style=Style(color=self.border))
        return line


def _wrap_lines(text: str, width: int) -> list[str]:
    """Word-wrap a possibly multi-line block into lines of <= ``width`` cells.

    Blank lines are preserved so a verification command pasted as a shell
    snippet keeps its shape.
    """
    if width <= 0:
        return []
    out: list[str] = []
    for raw in (text or "").splitlines() or [""]:
        line = raw.rstrip()
        if not line:
            out.append("")
            continue
        wrapped = Text(line, overflow="fold").wrap(_WRAP_CONSOLE, width, overflow="fold")
        out.extend(piece.plain for piece in wrapped)
    return out


# ---------------------------------------------------------------------------
# compact widget
# ---------------------------------------------------------------------------


def render_compact(
    service: GoalService,
    record: GoalRecord,
    *,
    width: int = FALLBACK_WIDTH,
    expanded_hint: str = "ctrl+shift+g",
    agents: list[SubagentRun] | None = None,
) -> Text:
    """The above-editor beacon, laid out to ``width`` terminal cells.

    ``width`` is the widget's real content width, so the box always closes on
    the right edge. Below :data:`MIN_BOX_WIDTH` the box chrome costs more
    than it communicates, so it degrades to a single status line.
    """
    width = int(width or FALLBACK_WIDTH)
    c = _colors()
    done, total = count_tasks(record.tasks)
    pct = round(done * 100 / total) if total else 0
    running_agents, _ = REGISTRY.counts()

    if width < MIN_BOX_WIDTH:
        return _render_terse(record, done, total, pct, running_agents, width)

    box = _Box(width, c.border)
    inner = box.inner
    agents = agents if agents is not None else REGISTRY.runs()
    active = current_task(record)

    text = Text()
    text.append(box.top())
    text.append("\n")

    # Header: [vtx-goal] ─ <objective>
    header = Text()
    header.append(title_chip())
    header.append(" ─ ", style=Style(color=c.border))
    used = cell_len(header.plain)
    header.append(_ellipsize(record.objective, inner - used), style=Style(color=c.title))
    text.append(box.row(header))
    text.append("\n")

    # Status: ● running  [12m47s 18.2K/200K]  (+2 open)
    status = Text()
    status.append(status_dot(record.status))
    status.append(record.label(), style=Style(color=status_style(record.status)))
    usage = format_usage(record)
    if usage:
        status.append(f"  {usage}", style=Style(color=c.dim))
    open_extra = len(service.pool()) - 1
    if open_extra > 0:
        status.append(f"  +{open_extra} open", style=Style(color=c.muted))
    text.append(box.row(status))
    text.append("\n")

    if total > 0:
        text.append(box.rule_row(_tasks_header(record, done, total, pct, inner)))
        text.append("\n")
        for mark, label in _task_lines_window(record, limit=_task_window_limit()):
            is_current = active is not None and label.startswith(f"{mark} {active.id}")
            body = Text()
            body.append(mark, style=Style(color=mark_color(mark), bold=is_current))
            body.append(
                label, style=Style(color=c.accent if is_current else c.fg, bold=is_current)
            )
            text.append(box.row(body))
            text.append("\n")

    agent_rows = _agent_rows(agents, inner)
    for row in agent_rows:
        text.append(box.row(row))
        text.append("\n")

    # Key/value rows, label-gutter aligned.
    rows: list[tuple[str, str, str]] = []
    if active is not None:
        rows.append(("Current", f"{active.id} · {active.title}", c.fg))
    elif total > 0 and done == total:
        rows.append(("Current", "all tasks complete", c.success))
    rows.append(("File", _file_label(service.cwd, record.id), c.muted))

    label_width = max(len(label) for label, _, _ in rows)
    for label, value, value_color in rows:
        line = Text()
        line.append(label.ljust(label_width), style=Style(color=c.dim))
        line.append("  ", style=Style(color=c.dim))
        line.append(_ellipsize(value, inner - label_width - 2), style=Style(color=value_color))
        text.append(box.row(line))
        text.append("\n")

    # Verification: wrapped, not clipped — a shell command you cannot read is
    # worse than no command at all.
    verification = record.verification.strip()
    if verification:
        head = "Verify" + " " * max(1, label_width + 2 - len("Verify"))
        room = inner - cell_len(head)
        for i, piece in enumerate(_wrap_lines(verification, room)):
            line = Text(head if i == 0 else " " * cell_len(head), style=Style(color=c.dim))
            line.append(piece, style=Style(color=c.notice))
            text.append(box.row(line))
            text.append("\n")

    text.append(
        box.footer(Text(f" Esc: pause   {expanded_hint}: expand", style=Style(color=c.dim)))
    )
    return text


def _render_terse(
    record: GoalRecord, done: int, total: int, pct: int, running: int, width: int
) -> Text:
    """Terminal too narrow for the box: one status line, no chrome."""
    c = _colors()
    out = Text()
    out.append(status_dot(record.status))
    out.append(record.label(), style=Style(color=status_style(record.status)))
    if total > 0:
        out.append(f"  {done}/{total} {pct}%", style=Style(color=c.dim))
    usage = format_usage(record)
    if usage:
        out.append(f"  {usage}", style=Style(color=c.dim))
    if running:
        out.append(f"  ⚙{running}", style=Style(color=c.accent, bold=True))
    room = width - cell_len(out.plain)
    if room > 4:
        out.append("  " + _ellipsize(record.objective, room - 2), style=Style(color=c.muted))
    return out


def _tasks_header(record: GoalRecord, done: int, total: int, pct: int, inner: int) -> Text:
    c = _colors()
    head = Text()
    head.append("Tasks ", style=Style(color=c.dim))
    head.append(f"✓{done}/{total}", style=Style(color=c.success, bold=True))
    head.append("  ", style=Style(color=c.dim))
    sub_done, sub_total = _subtask_progress(record)
    # Reserve room for the trailing " · sub a/b" before sizing the bar.
    suffix = f" · sub {sub_done}/{sub_total}" if sub_total > 0 else ""
    bar_width = max(4, min(12, inner - cell_len(head.plain) - cell_len(suffix) - 6))
    head.append(progress_bar_text(pct, bar_width))
    head.append(f" {pct}%", style=Style(color=c.accent, bold=True))
    if sub_total > 0:
        head.append(suffix, style=Style(color=c.dim))
    return head


def _task_window_limit() -> int:
    """Task rows shown in the beacon; the rest collapse to a `+N more` line."""
    return 5


def _agent_rows(agents: list[SubagentRun], inner: int) -> list[Text]:
    """Compact `Agents` block: a count line plus one line per live sub-agent."""
    if not agents:
        return []
    c = _colors()
    running, finished = REGISTRY.counts()
    rows: list[Text] = []

    head = Text("Agents ", style=Style(color=c.dim))
    if running:
        head.append(f"{running} running", style=Style(color=c.accent, bold=True))
        if finished:
            head.append(f" · {finished} done", style=Style(color=c.dim))
    else:
        head.append(f"{finished} done", style=Style(color=c.success))
    tokens = REGISTRY.total_tokens()
    if tokens:
        head.append(f"  {format_tokens(tokens)} tok", style=Style(color=c.dim))
    rows.append(head)

    # Running first: those are the ones the user is waiting on.
    live = sorted((r for r in agents if r.running), key=lambda r: r.started_at)[:3]
    for run in live:
        line = Text("  ")
        line.append("▸ ", style=Style(color=c.accent, bold=True))
        name = truncate_on_words(run.name, 18)
        line.append(name, style=Style(color=c.fg, bold=True))
        line.append("  ")
        detail = run.active_tool or run.top_tools() or run.description
        line.append(
            _ellipsize(detail, max(4, inner - cell_len(line.plain))), style=Style(color=c.dim)
        )
        rows.append(line)

    hidden = sum(1 for r in agents if r.running) - len(live)
    if hidden > 0:
        rows.append(Text(f"    +{hidden} more running", style=Style(color=c.muted)))
    return rows


def _subtask_progress(record: GoalRecord) -> tuple[int, int]:
    parent = current_task(record)
    if parent is None:
        return 0, 0
    subs = [t for t in record.tasks if t.parent_id == parent.id]
    if not subs:
        return 0, 0
    return sum(1 for t in subs if t.status == "complete"), len(subs)


def _task_lines_window(record: GoalRecord, limit: int = 5) -> list[tuple[str, str]]:
    """``(mark, label)`` rows for a window anchored to the newest completed task.

    Rows are returned un-truncated; :class:`_Box` clips them to the real
    width so the same renderer works at any terminal size.
    """
    top = [t for t in record.tasks if not t.parent_id]
    if not top:
        return []
    active = current_task(record)
    last_done = max((i for i, t in enumerate(top) if t.status == "complete"), default=-1)
    start = max(0, last_done - (limit - 2)) if len(top) > limit else 0
    window = top[start : start + limit]
    rows: list[tuple[str, str]] = []

    for task in window:
        mark = task_mark(record, task.id)
        flag = " ☑" if task.status == "complete" and task.evidence else ""
        rows.append((mark, f" {task.id}  {task.title}{flag}"))
        subs = [t for t in record.tasks if t.parent_id == task.id]
        if subs and active is not None and (active.id == task.id or active.parent_id == task.id):
            sub_active = current_task(record)
            for sub in subs[:3]:
                sub_mark = (
                    TASK_MARKS["current"]
                    if sub_active is not None and sub.id == sub_active.id
                    else task_mark(record, sub.id)
                )
                rows.append((sub_mark, f"   {sub.id}  {sub.title}"))

    if start > 0:
        rows.insert(0, (TASK_MARKS["pending"], " … earlier tasks hidden"))
    remaining = len(top) - (start + len(window))
    if remaining > 0:
        rows.append(
            (TASK_MARKS["pending"], f" … +{remaining} more task{'s' if remaining != 1 else ''}")
        )
    return rows


def _file_label(cwd: str, goal_id: str) -> str:
    from vtx.ai.agent.goal.storage import find_goal_file

    path = find_goal_file(cwd, goal_id)
    if path is None:
        return ".vtx/goals/"
    try:
        rel = path.relative_to(cwd)
    except ValueError:
        return str(path)
    return str(rel)


class GoalWidget(Static):
    """Above-editor goal beacon. Hidden when no goal is focused."""

    DEFAULT_CSS = """
    GoalWidget {
        display: none;
        padding: 0 1;
        margin: 0 1;
        height: auto;
    }
    GoalWidget.-visible {
        display: block;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._cwd = ""
        self._session_id = ""
        self._last_width = 0

    def set_cwd(self, cwd: str) -> None:
        self._cwd = cwd

    def set_session_id(self, session_id: str) -> None:
        self._session_id = session_id

    def on_resize(self, event) -> None:
        """Re-lay out on resize so the box always matches the real width."""
        width = getattr(event.size, "width", 0)
        if width and width != self._last_width:
            self._last_width = width
            self.refresh_goal()

    def _available_width(self) -> int:
        """Content width in cells, excluding our own horizontal padding."""
        for source in (self._last_width, self.content_size.width, self.container_size.width):
            if source:
                # DEFAULT_CSS applies `padding: 0 1` -> two cells of chrome.
                return max(0, source - 2)
        return FALLBACK_WIDTH

    def refresh_goal(self, cwd: str | None = None) -> None:
        """Re-render from disk state; hide when nothing is focused."""
        cwd = cwd or self._cwd
        if not cwd:
            self.remove_class("-visible")
            return
        service = get_service(cwd, self._session_id)
        record = service.focused() if not service.settings.get("disabled") else None
        if record is None:
            self.remove_class("-visible")
            self.update(Text(""))
            return
        self.add_class("-visible")
        self.update(render_compact(service, record, width=self._available_width()))


# ---------------------------------------------------------------------------
# Expanded dashboard
# ---------------------------------------------------------------------------


def render_expanded(
    service: GoalService,
    record: GoalRecord,
    *,
    activity_limit: int = 6,
    width: int = FALLBACK_WIDTH,
    agents: list[SubagentRun] | None = None,
) -> Text:
    """Full unified dashboard: progress, task tree, contracts, activity.

    Long-form prose (verification, auditor feedback, evidence) is emitted
    verbatim and word-wrapped. The chat log is the only place with room to
    show it in full, which is exactly why nothing here is clipped.
    """
    from vtx.ai.agent.goal.storage import recent_activity

    c = _colors()
    done, total = count_tasks(record.tasks)
    pct = round(done * 100 / total) if total else 0
    width = max(24, int(width or FALLBACK_WIDTH))
    agents = agents if agents is not None else REGISTRY.runs()

    text = Text()
    text.append(title_chip())
    text.append(" ─ ", style=Style(color=c.dim))
    used = cell_len(text.plain)
    text.append(_ellipsize(record.objective, width - used), style=Style(color=c.title))
    text.append("\n")
    text.append(status_dot(record.status))
    text.append(record.label(), style=Style(color=status_style(record.status)))
    meta = Text("  ", style=Style(color=c.dim))
    usage = format_usage(record)
    if usage:
        meta.append(f"{usage} · ", style=Style(color=c.dim))
    meta.append(f"id {record.id} · mode {record.mode}", style=Style(color=c.muted))
    text.append(meta)

    # Progress
    text.append("\n\n")
    text.append(section_header("Progress"))
    bar = Text("\n")
    bar.append(progress_bar_text(pct, 12))
    bar.append(f"  {done}/{top_level_total(record)} tasks", style=Style(color=c.fg))
    bar.append(f"  {pct}%", style=Style(color=c.accent, bold=True))
    text.append(bar)

    # Tasks
    text.append("\n\n")
    text.append(section_header("Tasks"))
    if record.tasks:
        text.append("\n")
        text.append(_task_tree_text(record, width))
    else:
        text.append("\n(no task plan)", style=Style(color=c.dim))

    # Current task
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
            _append_field(text, "Contract", contract, c.notice, width)
        if active.evidence:
            _append_field(text, "Evidence", active.evidence, c.success, width)
    else:
        text.append(
            "\n(none — all tasks complete)" if total else "\n(none)", style=Style(color=c.dim)
        )

    # Sub-agents dispatched during this run
    if agents:
        text.append("\n\n")
        text.append(section_header("Agents"))
        text.append_text(_agent_block(agents, width))

    # Goal verification
    text.append("\n\n")
    text.append(section_header("Verification", color=c.notice))
    if record.verification.strip():
        text.append("\n")
        text.append(record.verification.strip(), style=Style(color=c.notice))
    else:
        text.append(
            "\n(no contract — auditor judges against the objective)", style=Style(color=c.dim)
        )

    if record.review_feedback:
        text.append("\n\n")
        text.append(section_header("Auditor feedback", color=c.failed))
        text.append("\n")
        text.append(record.review_feedback.strip(), style=Style(color=c.failed))
        text.append(
            "\n\nAddress every item above, then complete the goal again.", style=Style(color=c.dim)
        )

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

    text.append(Text("\nFile: ", style=Style(color=c.muted)))
    text.append(_file_label(service.cwd, record.id), style=Style(color=c.border))
    return text


def _append_field(text: Text, label: str, value: str, color: str, width: int) -> None:
    """Append a `Label: value` field, wrapping value under a hanging indent."""
    text.append(f"\n{label}: ", style=Style(color=config.ui.colors.dim))
    gutter = len(label) + 2
    lines = _wrap_lines(value, max(8, width - gutter))
    for i, line in enumerate(lines):
        if i:
            text.append("\n" + " " * gutter)
        text.append(line, style=Style(color=color))


def top_level_total(record: GoalRecord) -> int:
    return sum(1 for t in record.tasks if not t.parent_id)


def _task_contract(task) -> str:
    note = task.note.strip()
    if note.lower().startswith("contract:"):
        return note[len("contract:") :].strip()
    return ""


def _agent_block(agents: list[SubagentRun], width: int) -> Text:
    """Expanded per-sub-agent list: outcome, turns, tokens, tool breakdown."""
    c = _colors()
    running, finished = REGISTRY.counts()
    out = Text()
    out.append(f"\n{running} running · {finished} finished", style=Style(color=c.dim))

    ordered = sorted(agents, key=lambda r: (not r.running, -r.started_at))
    name_width = min(20, max(10, max(cell_len(r.name) for r in ordered)))
    for run in ordered:
        out.append("\n")
        if run.running:
            out.append("▸ ", style=Style(color=c.accent, bold=True))
        else:
            icon = {"ok": "✓ ", "error": "✗ "}.get(run.status, "· ")
            out.append(
                icon,
                style=Style(
                    color={"ok": c.success, "error": c.failed}.get(run.status, c.dim), bold=True
                ),
            )
        out.append(
            truncate_on_words(run.name, name_width).ljust(name_width + 1),
            style=Style(color=c.fg, bold=True),
        )
        turns = f"↻{run.turns}" + (f"≤{run.max_turns}" if run.max_turns else "")
        parts = [turns, f"{format_tokens(run.tokens)} tok", f"{run.elapsed_ms / 1000:.1f}s"]
        if run.top_tools(3):
            parts.append(run.top_tools(3))
        out.append(" · ".join(parts), style=Style(color=c.dim))
        if run.error:
            for line in _wrap_lines(run.error, max(10, width - 4)):
                out.append("\n    " + line, style=Style(color=c.failed))
        elif run.description and not run.running:
            for line in _wrap_lines(run.description, max(10, width - 4)):
                out.append("\n    " + line, style=Style(color=c.muted))
    return out


def _task_tree_text(record: GoalRecord, width: int = FALLBACK_WIDTH) -> Text:
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
        title_room = max(12, width - (depth * 2 + 8 + len(task.id)))
        out.append(_ellipsize(task.title, title_room), style=style)
        out.append("\n")

        detail = ""
        detail_color = c.dim
        if task.status == "complete" and task.evidence:
            detail, detail_color = f"→ {task.evidence}", c.success
        elif task.status == "skipped" and task.note:
            detail = f"→ skipped: {task.note}"
        if not detail:
            return
        # Evidence gets the width the title left over, so the actual proof
        # survives instead of being clipped to a fixed character count.
        gutter = "    " + "  " * depth
        for i, line in enumerate(_wrap_lines(detail, max(20, width - cell_len(gutter)))):
            out.append(
                gutter + line if i == 0 else "\n" + gutter + line, style=Style(color=detail_color)
            )
        out.append("\n")

    for task in top:
        subs = [t for t in record.tasks if t.parent_id == task.id]
        emit(task, 0, last=False)
        for j, sub in enumerate(subs):
            emit(sub, 1, last=j == len(subs) - 1)
    return out
