"""Goal dashboard UI: the above-editor beacon plus renderers.

Two modes share one presentation model, so they can never disagree:

- compact: always visible above the editor while a goal is focused
- expanded: full task tree + verification + recent activity, rendered
  into the chat log via Ctrl+Shift+G

Render functions are pure (data in, Rich Text out), matching the
convention in :mod:`vtx.tui.task_ui`.

The rules the whole module follows:

- **A rail, not a box.** One accent-coloured ``▌`` down the left edge instead
  of a ``╭─╮`` frame. A frame spends two rows and two very loud rules saying
  "this is a panel", which the rail says with one cell — and it said it
  *loudest* on a wide terminal, where the horizontal rules are longest and the
  content they enclose is no wider than it would be without them.
- **One fact, one row.** Status, objective and accounting share the header
  line, because at any usable width that line is half empty while the rows
  below were paying to repeat it. The old `Current` row existed only to
  restate the ``▸`` the task list already showed.
- **Colour means state, not decoration.** Pending work is muted; orange is
  reserved for the verification contract. The mark column is the fastest read
  on the panel precisely because only three of five marks are coloured.
- **Every width is measured in terminal cells** (``cell_len``), never
  characters, so a CJK or emoji objective cannot shear the rail.
- **Nothing that matters is clipped.** One-line fields ellipsize on a word
  boundary, multi-line fields wrap, and the goal file always holds the full
  text. Ctrl+Shift+G is always one keypress away.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.console import Console
from rich.style import Style
from rich.text import Text
from textual.widgets import Static

from vtx.ai.agent.goal.record import (
    GoalRecord,
    TaskRecord,
    count_tasks,
    current_task,
    truncate_on_words,
)
from vtx.ai.agent.goal.service import GoalService, get_service
from vtx.ai.config import config
from vtx.tui.agents_panel import render_agents
from vtx.tui.goal_agents import REGISTRY, SubagentRun

if TYPE_CHECKING:
    from vtx.tui.themes import ColorsConfig

# Task markers: ✓ complete · ▸ current · ~ skipped · · pending
TASK_MARKS = {"complete": "✓", "current": "▸", "skipped": "~", "pending": "·"}

#: Width the compact beacon falls back to before the widget has been laid out
#: (and in tests that never mount the app).
FALLBACK_WIDTH = 80

#: Below this the rail costs more than it communicates, so the beacon degrades
#: to a single status line instead of drawing anything at all.
MIN_RAIL_WIDTH = 40

#: Objective shorter than this in the header is not worth truncating for; the
#: status and the accounting are the more useful half of the line.
MIN_OBJECTIVE_ROOM = 14

#: Task rows shown in the beacon; the rest collapse to a `+N more` line.
TASK_ROWS = 5

#: Subtask rows under the task in flight before the tail collapses. The
#: subtasks of one task are the only tree the beacon has room to show at all.
MAX_SUBTASK_ROWS = 3

#: Sub-agent rows shown in the beacon. The pinned Agents panel is the standing
#: view; inside the goal beacon the fan-out is a footnote, not the headline.
AGENT_ROWS = 2

#: Cells consumed by the rail itself: the accent bar and its trailing space.
RAIL = "▌"
RAIL_CELLS = 2

#: Reused console for width maths; never printed to.
_WRAP_CONSOLE = Console(width=FALLBACK_WIDTH)

#: `2026-09-27 11:15:02 · task t2 → start` -> ("11:15:02", "task t2 → start").
_ACTIVITY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+(\d{2}:\d{2}:\d{2})\s*·\s*")


def _colors() -> ColorsConfig:
    return config.ui.colors


def _style(color: str, *, bold: bool = False) -> Style:
    return Style(color=color, bold=bold)


# ---------------------------------------------------------------------------
# formatters
# ---------------------------------------------------------------------------


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


def format_duration(ms: float) -> str:
    """`12m47s`, `2m15s`, `3h04m` — never a bare `859.1s`."""
    seconds = int(max(0.0, ms) // 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{seconds:02d}s"


def format_usage(record: GoalRecord) -> str:
    """Elapsed time + tokens, e.g. `12m47s · 18.2K/200K`.

    Long runs reach the millions, so the token figure scales to M — a goal
    that has burned 27M tokens should not read as `27631.9K`.
    """
    total_ms = record.usage.elapsed_ms
    tokens = record.usage.total_tokens()
    if total_ms <= 0 and tokens <= 0:
        return ""
    token_text = format_tokens(tokens)
    if record.token_budget:
        token_text = f"{token_text}/{format_tokens(record.token_budget)}"
    return f"{format_duration(total_ms)} · {token_text}"


def progress_bar_text(pct: int, width: int = 8) -> Text:
    """A slim one-line bar: heavy fill over a hairline track.

    Half-cell rounding (``╸``) is what Textual's own bar uses, and it is the
    difference between a progress bar that appears to stutter in 8% jumps and
    one that creeps. ``█``/``░`` was the alternative and it reads as a chart at
    every width, which is a lot of ink for a percentage.
    """
    width = max(4, min(width, 64))
    exact = max(0.0, min(1.0, pct / 100.0)) * width
    filled = int(exact)
    c = _colors()
    bar = Style(color=c.success if pct >= 100 else c.accent, bold=True)
    out = Text()
    out.append("━" * filled, style=bar)
    if filled < width and exact - filled >= 0.5:
        out.append("╸", style=bar)
        filled += 1
    out.append("─" * (width - filled), style=Style(color=c.border))
    return out


def status_style(status: str) -> str:
    colors = _colors()
    return {
        "active": colors.success,
        "paused": colors.notice,
        "blocked": colors.failed,
        "budget_limited": colors.notice,
        "complete": colors.success,
    }.get(status, colors.fg)


def status_dot(status: str) -> Text:
    """Colored ● indicating goal status, driven by the active theme."""
    return Text("● ", style=_style(status_style(status), bold=True))


def active_task(record: GoalRecord):
    """The task in flight — only while the goal is actually running.

    A paused, blocked or budget-limited goal has nothing in flight, so nothing
    gets the accent-coloured ``▸``. Without this the beacon announced a
    "current" task for work that was not happening, and did it in the loudest
    style on the panel.
    """
    if record.status != "active":
        return None
    return current_task(record)


def task_mark(record: GoalRecord, task_id: str) -> str:
    task = next((t for t in record.tasks if t.id == task_id), None)
    if task is None:
        return TASK_MARKS["pending"]
    if task.status == "complete":
        return TASK_MARKS["complete"]
    if task.status == "skipped":
        return TASK_MARKS["skipped"]
    active = active_task(record)
    if active is not None and active.id == task.id:
        return TASK_MARKS["current"]
    return TASK_MARKS["pending"]


def mark_color(mark: str) -> str:
    """Colour for a task mark.

    Pending is muted on purpose: most rows in a running plan are pending, and
    painting them all in the warning colour made the beacon look like a wall
    of alerts. Orange is now spent only on the verification contract.
    """
    colors = _colors()
    return {
        TASK_MARKS["complete"]: colors.success,
        TASK_MARKS["current"]: colors.accent,
        TASK_MARKS["skipped"]: colors.dim,
        TASK_MARKS["pending"]: colors.muted,
    }.get(mark, colors.fg)


def title_chip(label: str = "vtx-goal") -> Text:
    """Badge-style label chip using the theme's badge colors."""
    c = _colors()
    return Text(f" {label} ", style=Style(color=c.badge.label, bgcolor=c.badge.bg, bold=True))


def section_header(title: str, color: str | None = None) -> Text:
    """Quiet `◆ Title` eyebrow for the expanded dashboard.

    The diamond is a hairline colour and the word carries the weight, so a
    section marker reads as structure rather than as another thing competing
    with the content for attention.
    """
    c = _colors()
    out = Text()
    out.append("◆ ", style=Style(color=color or c.border))
    out.append(title, style=_style(color or c.title, bold=True))
    return out


# ---------------------------------------------------------------------------
# width helpers
# ---------------------------------------------------------------------------


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


def _join(segments: Sequence[tuple[str, str, bool]], width: int, sep: str = "  ") -> Text:
    """Render ``(text, color, bold)`` segments joined by ``sep``.

    Segments are dropped from the *right* until the line fits, because the
    left of a metrics run is the part that has already been read (the elapsed
    time you have been watching) and the right is the first thing worth losing.
    """
    parts = list(segments)
    while parts:
        candidate = _join_exact(parts, sep)
        if cell_len(candidate.plain) <= width:
            return candidate
        parts.pop()
    return Text("")


def _join_exact(segments: Sequence[tuple[str, str, bool]], sep: str = "  ") -> Text:
    out = Text()
    for index, (text, color, bold) in enumerate(segments):
        if index:
            out.append(sep)
        out.append(text, style=_style(color, bold=bold))
    return out


class _Rail:
    """Rows hung off an accent rail, forced to exactly ``width`` cells.

    Every row is emitted as ``▌ <inner>`` and fitted to the real inner width,
    so an over-long label or a double-width CJK character can never shear the
    edge. Doing that arithmetic at each call site is what made the old box go
    ragged, so it lives in one place instead.
    """

    def __init__(self, width: int, color: str) -> None:
        self.width = max(8, width)
        self.color = color
        self.inner = max(1, self.width - RAIL_CELLS)

    def row(self, content: Text) -> Text:
        line = Text(RAIL, style=Style(color=self.color))
        line.append(" ")
        line.append_text(_fit(content, self.inner))
        return line

    def gap(self) -> Text:
        """An empty row that still carries the rail, so the edge stays unbroken."""
        return Text("")


def _keyhint(pairs: Sequence[tuple[str, str]], width: int) -> Text:
    """`esc pause  ·  ctrl+shift+g expand`, right-aligned as a footer.

    Right-aligned rather than stacked under the content: it reads as chrome
    instead of as another field, and it costs no row.
    """
    c = _colors()
    out = Text()
    for index, (key, action) in enumerate(pairs):
        if index:
            out.append("  ·  ", style=Style(color=c.border))
        out.append(key, style=Style(color=c.muted))
        out.append(" " + action, style=Style(color=c.dim))
    if cell_len(out.plain) > width:
        out = Text("")
    return Text(" " * max(0, width - cell_len(out.plain))) + out


# ---------------------------------------------------------------------------
# compact widget
# ---------------------------------------------------------------------------


def _header(record: GoalRecord, *, width: int, open_extra: int = 0) -> Text:
    """One line carrying identity, state, objective and accounting.

    The objective flexes in the gap between the two fixed halves, so on a wide
    terminal it gets the space instead of trailing whitespace.
    """
    c = _colors()
    status_color = status_style(record.status)
    segments: list[tuple[str, str, bool]] = []
    usage = format_usage(record)
    if usage:
        segments.append((usage, c.dim, False))
    if open_extra > 0:
        segments.append((f"+{open_extra} open", c.muted, False))
    right = _join(segments, max(0, width // 2))

    line = Text()
    line.append(title_chip())
    line.append(" ")
    line.append(status_dot(record.status))
    line.append(record.label(), style=_style(status_color))
    room = width - cell_len(line.plain) - cell_len(right.plain) - 2
    if room >= MIN_OBJECTIVE_ROOM:
        line.append("  " + _ellipsize(record.objective, room - 2), style=_style(c.title))
    line.append(" " * max(1, width - cell_len(line.plain) - cell_len(right.plain)))
    line.append_text(right)
    return line


def _progress_line(record: GoalRecord, *, width: int) -> Text:
    """Bar, counts, and the subtask roll-up for the task in flight.

    Returns an empty row when there is no plan yet: a ``0/0 · 0%`` bar is a
    chart of nothing, and the header already says the goal is running.
    """
    c = _colors()
    done, total = count_tasks(record.tasks)
    if not total:
        return Text("")
    pct = pct_of(done, total)
    sub_done, sub_total = _subtask_progress(record)
    tail = _join(
        [(f"tasks {done}/{total}", c.fg, False), (f"{pct}%", c.accent, True)]
        + ([(f"sub {sub_done}/{sub_total}", c.dim, False)] if sub_total else []),
        max(0, width // 2),
        sep=" · ",
    )
    bar_width = max(4, min(24, width - cell_len(tail.plain) - 2))
    line = Text()
    line.append_text(progress_bar_text(pct, bar_width))
    line.append("  ")
    line.append_text(tail)
    return line


@dataclass(frozen=True)
class _Collapsed:
    """A collapsed run of tasks, standing in for rows that did not fit."""

    count: int
    label: str


_TaskRow = tuple[TaskRecord, int] | _Collapsed


def _window_tasks(rows: list[_TaskRow]) -> list[TaskRecord]:
    """The real tasks in a window, dropping the collapsed-run markers."""
    tasks: list[TaskRecord] = []
    for entry in rows:
        if isinstance(entry, _Collapsed):
            continue
        task, _ = entry
        tasks.append(task)
    return tasks


def _task_window(record: GoalRecord, limit: int) -> tuple[list[_TaskRow], int, int]:
    """``(rows, hidden_before, hidden_after)`` for a window on the task plan.

    The window is centred on the *active* task rather than on the last
    completed one, so a reader always sees what is being worked on: the old
    anchoring could scroll the one row that matters off the bottom, which is
    why the beacon needed a second `Current` row to restate it.
    """
    top = [t for t in record.tasks if not t.parent_id]
    if not top:
        return [], 0, 0
    if len(top) <= limit:
        start, stop = 0, len(top)
    else:
        active = active_task(record)
        anchor = 0
        if active is not None and not active.parent_id:
            anchor = next((i for i, t in enumerate(top) if t.id == active.id), 0)
        start = max(0, min(anchor - limit // 2, len(top) - limit))
        stop = start + limit
    window = top[start:stop]

    # Subtasks hang off whichever task owns the current focus — the task itself
    # when it is top-level, otherwise the parent. Appending them after the
    # window instead would strand them under whichever task happened to be
    # last, which is how a reader ends up believing the wrong plan.
    active = active_task(record)
    parent_id = None
    if active is not None:
        parent_id = active.parent_id or active.id
    rows: list[_TaskRow] = []
    for task in window:
        rows.append((task, 0))
        if parent_id == task.id:
            subs = [sub for sub in record.tasks if sub.parent_id == task.id]
            rows.extend((sub, 1) for sub in subs[:MAX_SUBTASK_ROWS])
            if len(subs) > MAX_SUBTASK_ROWS:
                rows.append(_Collapsed(count=len(subs) - MAX_SUBTASK_ROWS, label="more"))
    return rows, start, len(top) - stop


def _task_line(
    mark: str,
    task_id: str,
    title: str,
    *,
    depth: int,
    id_width: int,
    current: bool,
    color: str,
    width: int,
) -> Text:
    line = Text()
    line.append("  " * depth)
    line.append(mark, style=_style(mark_color(mark), bold=current))
    line.append(" ")
    line.append(task_id.ljust(id_width), style=_style(color, bold=current))
    line.append("  ")
    room = width - depth * 2 - id_width - 4
    line.append(_ellipsize(title, room), style=_style(color, bold=current))
    return line


def _count_line(label: str, count: int) -> Text:
    """A collapsed-window row: `  … 3 earlier`, sitting in the id column."""
    c = _colors()
    line = Text()
    line.append(" ")
    line.append(f"… {count} {label}", style=Style(color=c.muted))
    return line


def _task_rows(record: GoalRecord, *, limit: int, width: int) -> list[Text]:
    """Beacon task rows: mark column, id column, flexible title."""
    c = _colors()
    rows, hidden_before, hidden_after = _task_window(record, limit)
    if not rows and not hidden_before and not hidden_after:
        return []
    active = active_task(record)
    active_id = active.id if active is not None else None
    id_width = max((cell_len(task.id) for task in _window_tasks(rows)), default=0)

    out: list[Text] = []
    if hidden_before:
        out.append(_count_line("earlier", hidden_before))
    for entry in rows:
        if isinstance(entry, _Collapsed):
            out.append(_count_line(entry.label, entry.count))
            continue
        task, depth = entry
        is_current = task.id == active_id
        out.append(
            _task_line(
                task_mark(record, task.id),
                task.id,
                task.title,
                depth=depth,
                id_width=id_width,
                current=is_current,
                color=c.accent if is_current else (c.muted if task.status == "skipped" else c.fg),
                width=width,
            )
        )
    if hidden_after:
        out.append(_count_line("more", hidden_after))
    return out


def _verify_rows(verification: str, *, width: int) -> list[Text]:
    """The verification contract, wrapped under a fixed label gutter.

    Wrapped, never clipped: a shell command you cannot read is worse than no
    command at all, and the goal file still holds it either way.
    """
    c = _colors()
    gutter = len("Verify")
    rows: list[Text] = []
    for index, piece in enumerate(_wrap_lines(verification, max(8, width - gutter - 2))):
        line = Text()
        line.append(
            ("Verify" if index == 0 else " " * gutter).ljust(gutter + 2), style=Style(color=c.dim)
        )
        line.append(piece, style=_style(c.notice))
        rows.append(line)
    return rows


def render_compact(
    service: GoalService,
    record: GoalRecord,
    *,
    width: int = FALLBACK_WIDTH,
    expanded_hint: str = "ctrl+shift+g",
    agents: list[SubagentRun] | None = None,
) -> Text:
    """The above-editor beacon, laid out to ``width`` terminal cells.

    ``width`` is the widget's real content width, so the rail always closes on
    the right edge. Below :data:`MIN_RAIL_WIDTH` the rail costs more than it
    communicates, so it degrades to a single status line.
    """
    width = int(width or FALLBACK_WIDTH)
    agents = agents if agents is not None else REGISTRY.runs()

    if width < MIN_RAIL_WIDTH:
        live = sum(1 for run in agents if not run.finished)
        return _render_terse(record, live, width)

    rail = _Rail(width, status_style(record.status))
    inner = rail.inner
    task_rows = _task_rows(record, limit=TASK_ROWS, width=inner)
    agent_rows = _agent_rows(agents, inner)
    verify_rows = (
        _verify_rows(record.verification.strip(), width=inner)
        if record.verification.strip()
        else []
    )

    body: list[Text] = [_header(record, width=inner, open_extra=max(0, len(service.pool()) - 1))]
    progress = _progress_line(record, width=inner)
    if progress.plain:
        body.append(progress)
    if task_rows:
        body.append(rail.gap())
        body.extend(task_rows)
    if agent_rows:
        body.append(rail.gap())
        body.extend(agent_rows)
    if verify_rows:
        body.append(rail.gap())
        body.extend(verify_rows)
    body.append(_keyhint((("esc", "pause"), (expanded_hint, "expand")), inner))

    out = Text()
    for row in body:
        out.append_text(rail.row(row))
        out.append("\n")
    out.right_crop()
    return out


def _render_terse(record: GoalRecord, live: int, width: int) -> Text:
    """Terminal too narrow for a rail: one status line, no chrome.

    Cells are budgeted rather than assumed. The previous version appended a
    fixed set of segments and only guarded the objective, so a running goal on a
    24-cell terminal printed a 37-cell line and let the terminal wrap it. Here
    the objective gets a reservation first, then each segment is taken in
    priority order if it still fits — a segment that does not fit is skipped
    rather than ending the line, so a long token count cannot hide the short
    progress figure behind it.
    """
    c = _colors()
    done, total = count_tasks(record.tasks)
    out = Text()
    out.append(status_dot(record.status))
    out.append(record.label(), style=_style(status_style(record.status)))

    used = cell_len(out.plain)
    # Only reserve for the objective when there is room for a readable stub of
    # it. Reserving too eagerly traded a scannable `50%` for `Migrate…`.
    reserve = 8 if width - used >= 24 else 0
    segments: list[tuple[str, str, bool]] = []
    if total > 0:
        segments.append((f"{done}/{total}", c.dim, False))
        segments.append((f"{pct_of(done, total)}%", c.accent, True))
    tokens = record.usage.total_tokens()
    elapsed = format_duration(record.usage.elapsed_ms) if record.usage.elapsed_ms > 0 else ""
    if elapsed:
        segments.append((elapsed, c.dim, False))
    if tokens:
        budget = f"/{format_tokens(record.token_budget)}" if record.token_budget else ""
        segments.append((f"{format_tokens(tokens)}{budget}", c.dim, False))
    if live:
        segments.append((f"⚙{live}", c.accent, True))

    for text, color, bold in segments:
        if used + 2 + cell_len(text) + reserve > width:
            continue
        out.append("  ")
        out.append(text, style=_style(color, bold=bold))
        used += 2 + cell_len(text)

    room = width - used - 2
    if room >= 6:
        out.append("  " + _ellipsize(record.objective, room), style=Style(color=c.muted))
    return out


def pct_of(done: int, total: int) -> int:
    return round(done * 100 / total) if total else 0


def _agent_rows(agents: list[SubagentRun], inner: int) -> list[Text]:
    """Compact sub-agent block: live rows, then the queued/finished counts.

    Shares :func:`~vtx.tui.agents_panel.render_agents` with the pinned panel
    so the beacon and the standing view never disagree about a run. The tree
    gutter is suppressed: the rail already provides the vertical, and drawing
    both gave the beacon a `│ │ ├─` double edge.
    """
    if not any(not run.finished for run in agents):
        return []
    rendered = render_agents(agents, width=inner, max_rows=AGENT_ROWS, header=False, gutter="")
    return [row for row in rendered.split("\n") if row.plain.strip()]


def _subtask_progress(record: GoalRecord) -> tuple[int, int]:
    parent = current_task(record)
    if parent is None:
        return 0, 0
    subs = [t for t in record.tasks if t.parent_id == parent.id]
    if not subs:
        return 0, 0
    return sum(1 for t in subs if t.status == "complete"), len(subs)


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

    #: Cadence while a sub-agent is in flight. The beacon shares the registry
    #: with the pinned panel, so a slower tick here shows a fan-out frozen
    #: mid-tool for seconds at a time.
    LIVE_TICK_SECONDS = 0.5

    #: Cadence with nothing in flight: the goal file can still change under us
    #: (the auditor, a sibling session), but not by the second.
    IDLE_TICK_SECONDS = 5.0

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._cwd = ""
        self._session_id = ""
        self._last_width = 0
        self._timer = None
        self._timer_interval = 0.0

    def set_cwd(self, cwd: str) -> None:
        self._cwd = cwd

    def set_session_id(self, session_id: str) -> None:
        self._session_id = session_id

    def on_mount(self) -> None:
        self._set_tick(self.IDLE_TICK_SECONDS)

    def on_unmount(self) -> None:
        self._stop_tick()

    def _set_tick(self, interval: float) -> None:
        if self._timer is not None and self._timer_interval == interval:
            return
        self._stop_tick()
        self._timer_interval = interval
        self._timer = self.set_interval(interval, self.refresh_goal)

    def _stop_tick(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
            self._timer_interval = 0.0

    def on_resize(self, event) -> None:
        """Re-lay out on resize so the rail always matches the real width."""
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
            self._stop_tick()
            return
        service = get_service(cwd, self._session_id)
        record = service.focused() if not service.settings.get("disabled") else None
        if record is None:
            self.remove_class("-visible")
            self.update(Text(""))
            self._stop_tick()
            return
        self.add_class("-visible")
        self._set_tick(self.LIVE_TICK_SECONDS if REGISTRY.has_live() else self.IDLE_TICK_SECONDS)
        self.update(render_compact(service, record, width=self._available_width()))


# ---------------------------------------------------------------------------
# expanded dashboard
# ---------------------------------------------------------------------------


def _append_field(text: Text, rail: _Rail, label: str, value: str, color: str) -> None:
    """Append a ``label  value`` field, hanging wrapped lines under the label.

    The label keeps its own column so a long verification command stays
    visibly attached to the field it belongs to instead of drifting left.
    """
    gutter = len(label) + 2
    lines = _wrap_lines(value, max(8, rail.inner - gutter))
    for index, line in enumerate(lines):
        row = Text()
        if index == 0:
            row.append(label, style=Style(color=config.ui.colors.dim))
        row.append("  ")
        row.append(line, style=_style(color))
        text.append("\n")
        text.append_text(rail.row(row))


def top_level_total(record: GoalRecord) -> int:
    """Count of top-level tasks — the plan's headline number.

    Subtasks are not counted: the plan the agent committed to is the list of
    top-level tasks, and inflating the denominator with subtasks made a goal
    look further from done every time it decomposed a task.
    """
    return sum(1 for t in record.tasks if not t.parent_id)


def _task_contract(task) -> str:
    note = task.note.strip()
    if note.lower().startswith("contract:"):
        return note[len("contract:") :].strip()
    return ""


def _status_reason(record: GoalRecord) -> str:
    """Why the goal is not running, in the user's own words.

    The status is on the header line; the *reason* was only in the goal file
    and the tool snapshot, so a blocked goal looked identical to a paused one.
    """
    if record.status == "blocked" and record.blocked_reason:
        return record.blocked_reason.strip()
    if record.status == "paused" and record.paused_reason:
        return record.paused_reason.strip()
    if record.status == "budget_limited":
        return "token budget reached — wrap up or raise the budget in the goal file"
    return ""


def _task_tree_text(record: GoalRecord, width: int = FALLBACK_WIDTH) -> Text:
    """The full plan: one aligned row per task, evidence under its title.

    Nothing here is clipped — the chat log is the only place with room to show
    a task's proof in full, which is exactly why the title yields first.
    """
    c = _colors()
    active = active_task(record)
    top = [t for t in record.tasks if not t.parent_id]
    id_width = max((cell_len(t.id) for t in record.tasks), default=0)
    out = Text()

    def emit(task, depth: int) -> None:
        is_current = active is not None and active.id == task.id
        color = c.accent if is_current else (c.dim if task.status == "skipped" else c.fg)
        prefix = 2 * depth + 2 + id_width + 2
        out.append_text(
            _task_line(
                task_mark(record, task.id),
                task.id,
                task.title,
                depth=depth,
                id_width=id_width,
                current=is_current,
                color=color,
                width=width,
            )
        )
        out.append("\n")

        detail, detail_color = "", c.dim
        if task.status == "complete" and task.evidence:
            detail, detail_color = f"→ {task.evidence}", c.success
        elif task.status == "skipped" and task.note:
            detail, detail_color = f"→ skipped: {task.note}", c.dim
        elif task.note and not _task_contract(task):
            detail, detail_color = f"→ {task.note}", c.muted
        if not detail:
            return
        # Evidence gets the width the title left over, so the actual proof
        # survives instead of being clipped to a fixed character count.
        for index, line in enumerate(_wrap_lines(detail, max(20, width - prefix))):
            if index:
                out.append("\n")
            out.append(" " * prefix, style=Style(color=detail_color))
            out.append(line, style=Style(color=detail_color))
        out.append("\n")

    for task in top:
        emit(task, 0)
        for sub in [t for t in record.tasks if t.parent_id == task.id]:
            emit(sub, 1)
    return out


def _agent_block(agents: list[SubagentRun], width: int) -> Text:
    """Expanded sub-agent list: outcome, turns, tokens, tool breakdown.

    Queued agents get a count instead of rows, matching the pinned panel: a row
    for an agent that has not started can only say `0 turns · 0 tokens`, which
    is noise, and two identically-named queued runs are indistinguishable on
    screen anyway.
    """
    c = _colors()
    _, queued, finished = REGISTRY.counts()
    out = Text()
    ordered = sorted(agents, key=lambda r: (r.finished, not r.running, r.dispatched_at))
    name_width = min(20, max(10, max((cell_len(r.name) for r in ordered), default=0)))

    for run in ordered:
        if run.queued:
            continue
        out.append("\n")
        if run.running:
            out.append("▸ ", style=_style(c.accent, bold=True))
        else:
            out.append(
                {"ok": "✓ ", "error": "✗ "}.get(run.status, "· "),
                style=_style(
                    {"ok": c.success, "error": c.failed}.get(run.status, c.dim), bold=True
                ),
            )
        out.append(
            truncate_on_words(run.name, name_width).ljust(name_width + 1),
            style=_style(c.fg, bold=True),
        )
        parts = [f"↻{run.turns}" + (f"≤{run.max_turns}" if run.max_turns else "")]
        parts.append(f"{format_tokens(run.tokens)} tok")
        parts.append(f"{run.elapsed_ms / 1000:.1f}s")
        if run.top_tools(3):
            parts.append(run.top_tools(3))
        out.append("  " + "  ·  ".join(parts), style=Style(color=c.dim))
        body = run.error or (run.description if not run.running else "")
        if body:
            for line in _wrap_lines(body, max(10, width - 6)):
                out.append("\n    " + line, style=Style(color=c.failed if run.error else c.muted))

    footer: list[tuple[str, str]] = []
    if queued:
        footer.append((f"○ {queued} queued", c.muted))
    if finished:
        tokens = sum(r.tokens for r in agents if r.finished)
        total = f"  ·  {format_tokens(tokens)}" if tokens else ""
        footer.append((f"✓ {finished} finished{total}", c.dim))
    if footer:
        out.append("\n")
        out.append("  ")
        out.append_text(
            _join([(text, color, False) for text, color in footer], max(0, width - 4), sep="  ·  ")
        )
    return out


def _activity_rows(activity: list[str], width: int) -> list[Text]:
    """Ledger tail with the date dropped and the times in one column.

    Every event in a goal run comes from the same sitting, so the leading
    ``2026-09-27`` was six lines of identical, unread dates in a column wide
    enough to hold the message.
    """
    c = _colors()
    rows: list[Text] = []
    for raw in activity:
        match = _ACTIVITY_RE.match(raw)
        stamp, message = (match.group(1), raw[match.end() :]) if match else ("", raw)
        line = Text()
        line.append((stamp if stamp else "").ljust(8) + " ", style=Style(color=c.border))
        line.append(_ellipsize(message, max(10, width - 11)), style=Style(color=c.dim))
        rows.append(line)
    return rows


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
    verbatim and word-wrapped, under a hanging indent that lines the wrapped
    text up under the field it belongs to.
    """
    from vtx.ai.agent.goal.storage import recent_activity

    c = _colors()
    done, total = count_tasks(record.tasks)
    pct = pct_of(done, total)
    width = max(24, int(width or FALLBACK_WIDTH))
    agents = agents if agents is not None else REGISTRY.runs()
    rail = _Rail(width, status_style(record.status))
    body = width - RAIL_CELLS

    out = Text()
    out.append_text(
        rail.row(_header(record, width=body, open_extra=max(0, len(service.pool()) - 1)))
    )

    meta = Text()
    meta.append(f"id {record.id}  ·  {record.mode}", style=Style(color=c.muted))
    if record.created_at:
        meta.append(f"  ·  {record.created_at[:10]}", style=Style(color=c.border))
    out.append("\n")
    out.append_text(rail.row(meta))

    reason = _status_reason(record)
    if reason:
        out.append("\n")
        note = Text()
        note.append(
            "why  ", style=Style(color=c.failed if record.status == "blocked" else c.notice)
        )
        note.append(_ellipsize(reason, body - 6), style=Style(color=c.muted))
        out.append_text(rail.row(note))

    def section(title: str, color: str | None = None) -> None:
        out.append("\n\n")
        out.append_text(rail.row(section_header(title, color)))

    def body_text(text: Text) -> None:
        out.append("\n")
        out.append_text(rail.row(text))

    # Progress
    section("Progress")
    if total:
        bar = Text()
        bar.append_text(progress_bar_text(pct, min(24, body)))
        bar.append("  ")
        bar.append(f"{done}/{total} tasks", style=_style(c.fg))
        bar.append("  ·  ")
        bar.append(f"{pct}%", style=_style(c.accent, bold=True))
        body_text(bar)
    else:
        # The section stays: it is where a reader looks to answer "how far
        # along is this". An empty bar would answer it badly, so say the plain
        # fact instead.
        body_text(Text("no task plan yet", style=Style(color=c.dim)))

    # Tasks
    section("Tasks")
    if record.tasks:
        out.append("\n")
        for row in _task_tree_text(record, body).split("\n"):
            out.append_text(rail.row(row))
    else:
        body_text(Text("(no task plan)", style=Style(color=c.dim)))

    # Current task
    section("Current task")
    active = current_task(record)
    if active is not None:
        head = Text()
        head.append(active.id, style=_style(c.accent, bold=True))
        head.append("  ")
        head.append(
            _ellipsize(active.title, max(8, body - cell_len(active.id) - 2)), style=_style(c.fg)
        )
        body_text(head)
        subs = [t for t in record.tasks if t.parent_id == active.id]
        if subs:
            sub_done = sum(1 for t in subs if t.status == "complete")
            sub_pct = pct_of(sub_done, len(subs))
            roll = Text()
            roll.append("  ")
            roll.append_text(progress_bar_text(sub_pct, 12))
            roll.append("  ")
            roll.append_text(
                _join(
                    [
                        (f"subtasks {sub_done}/{len(subs)}", c.fg, False),
                        (f"{sub_pct}%", c.accent, True),
                    ],
                    max(0, body - 16),
                    sep="  ·  ",
                )
            )
            body_text(roll)
        contract = _task_contract(active)
        if contract:
            _append_field(out, rail, "contract", contract, c.notice)
        if active.evidence:
            _append_field(out, rail, "evidence", active.evidence, c.success)
    else:
        body_text(
            Text("(none — all tasks complete)" if total else "(none)", style=Style(color=c.dim))
        )

    # Sub-agents dispatched during this run
    if agents:
        section("Agents")
        for row in _agent_block(agents, body).split("\n"):
            if row:
                out.append("\n")
                out.append_text(rail.row(row))

    # Goal verification
    section("Verification", color=c.notice)
    if record.verification.strip():
        _append_field(out, rail, "verify", record.verification.strip(), c.notice)
    else:
        body_text(
            Text("no contract — auditor judges against the objective", style=Style(color=c.dim))
        )

    if record.review_feedback:
        section("Auditor feedback", color=c.failed)
        out.append("\n")
        for line in record.review_feedback.strip().splitlines():
            out.append_text(rail.row(Text(line, style=Style(color=c.failed))))
        out.append("\n")
        out.append_text(
            rail.row(
                Text(
                    "address every item above, then complete the goal again",
                    style=Style(color=c.dim),
                )
            )
        )

    # Recent activity
    section("Activity")
    rows = _activity_rows(recent_activity(service.cwd, record.id, limit=activity_limit), body)
    if rows:
        for row in rows:
            out.append("\n")
            out.append_text(rail.row(row))
    else:
        body_text(Text("(no recorded activity yet)", style=Style(color=c.dim)))

    out.append("\n\n")
    foot = Text()
    foot.append("file  ", style=Style(color=c.border))
    foot.append(_file_label(service.cwd, record.id), style=Style(color=c.muted))
    out.append_text(rail.row(foot))
    return out
