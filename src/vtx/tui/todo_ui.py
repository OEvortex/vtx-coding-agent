"""The todo list, drawn — the one place both todo surfaces render from.

The ``todo`` tool paints twice: as the pinned strip above the editor
(:mod:`vtx.tui.todo_panel`) and as the in-chat tool block
(:mod:`vtx.tui.todo_block`). Before this module each drew its own list, so the
same tasks appeared in two different shapes a few lines apart on screen. Both
now build from the functions here, which is the rule the sub-agent strip set
for itself: *"every strip above the editor starts its rows with this, so a
reader moving between them is reading one panel and not two."*

Renderers are pure functions of ``(tasks, width, frame)``. ``frame`` is the
animation counter the panel advances on a timer — the in-progress row is a
spinner rather than a static half-filled circle, because a row that is
actually moving is the difference between "the agent is working" and "the
agent is thinking about working". Glyphs are chosen to agree with the sub-agent
strip (:mod:`vtx.tui.agents_panel`), so ``⠹`` and ``✓`` mean the same thing in
both panels.

Three decisions are worth stating, because the obvious alternative is wrong:

- **Insertion order is preserved, in both surfaces.** Codex sorts the
  in-progress row to the top and Crush sorts completed rows first. Both look
  tidier in a static screenshot, and both reorder the list on every update —
  which is exactly the wrong behaviour for a strip that repaints several times
  a second while the agent works. The running row is already the only
  accented, bold line on screen; it does not need to be first to be found.
- **The dependency badge is ``← #3``, not a chain glyph.** ``⛓`` (U+26D3) is an
  emoji-presentation codepoint: terminals that measure it two cells wide and
  render it one produce a line that repaints itself every frame. Crush
  documents the same trap (a trailing U+FE0F makes ``ansi.StringWidth``
  disagree with the terminal). Arrows and boxes are text presentation
  everywhere.
- **Completed rows are dim *and* struck.** Dim alone reads as disabled,
  which a finished task is not; strike alone disappears into a dark theme.
  Together they say "already dealt with" without spending a colour.

Width is measured in cells, not codepoints, and every row is ellipsized to the
width it was actually given, so a long subject can never push a strip wider
than the terminal it is measured against.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rich.cells import cell_len
from rich.style import Style
from rich.text import Text

from vtx.core.config import config
from vtx.tui.agents_panel import TREE_EDGE, TREE_GUTTER, TREE_LAST, strip_header, tree_prefix
from vtx.tui.task_ui import SPINNER

#: Status glyphs. ``pending`` and ``completed`` match the sub-agent strip's
#: queued/finished markers so one legend covers both panels.
GLYPH_PENDING = "○"  # U+25CB WHITE CIRCLE
GLYPH_COMPLETED = "✓"  # U+2713 CHECK MARK
GLYPH_BLOCKED = "⊘"  # U+2298 CIRCLED DIVISION SLASH

#: A pending task with an unmet dependency. Pending-but-blocked is a different
#: state from pending-and-next, and the agent re-plans differently depending on
#: which one it is looking at.
GLYPH_IN_PROGRESS = SPINNER[0]

#: Rows drawn in the pinned strip before the rest collapse to ``+N more``. The
#: strip sits above the editor, not in the scrollback, so it must not grow
#: until it pushes the prompt box off screen.
MAX_ROWS = 6

#: Cells of progress bar in the strip header. Wide enough to read the fill,
#: short enough that the counter beside it stays on one line at 60 columns.
BAR_CELLS = 10

#: Cells available for a subject before it is ellipsized. Generous: unlike an
#: agent row, a todo subject has nothing to share the line with.
MIN_SUBJECT_WIDTH = 12


def _colors() -> Any:
    try:
        return config.ui.colors
    except Exception:
        return None


def active_tasks(tasks: Sequence[Any]) -> list[Any]:
    """Tasks worth drawing: everything not tombstoned."""
    return [t for t in tasks if getattr(t, "status", "") != "deleted"]


def counts(tasks: Sequence[Any]) -> tuple[int, int]:
    """``(completed, total)`` for the header counter and the progress bar."""
    live = active_tasks(tasks)
    return sum(1 for t in live if t.status == "completed"), len(live)


def current_task(tasks: Sequence[Any]) -> Any | None:
    """The single ``in_progress`` task, if the agent has marked one."""
    return next((t for t in active_tasks(tasks) if t.status == "in_progress"), None)


def blocked_ids(tasks: Sequence[Any]) -> set[int]:
    """Ids of pending tasks still waiting on an unfinished dependency.

    A dependency that is itself blocked does not unblock anything, so this is
    a plain set lookup rather than a topological walk: the point is to mark the
    row, and a task whose blocker is unfinished is blocked no matter how deep
    the chain goes.
    """
    live = active_tasks(tasks)
    unfinished = {t.id for t in live if t.status != "completed"}
    blocked: set[int] = set()
    for task in live:
        if task.status != "pending":
            continue
        if any(dep in unfinished for dep in getattr(task, "blocked_by", ()) or ()):
            blocked.add(task.id)
    return blocked


def status_glyph(status: str, *, frame: int, blocked: bool = False) -> tuple[str, str]:
    """``(glyph, color)`` for one task row's marker.

    The in-progress marker animates: it is the same braille spinner the
    sub-agent strip uses for a running row, so "working" looks the same in
    both panels.
    """
    colors = _colors()
    if status == "completed":
        return GLYPH_COMPLETED, (colors.success if colors else "#00ff88")
    if status == "in_progress":
        return SPINNER[frame % len(SPINNER)], (colors.accent if colors else "#00b4ff")
    if blocked:
        return GLYPH_BLOCKED, (colors.notice if colors else "#ffb347")
    return GLYPH_PENDING, (colors.dim if colors else "#6b7280")


def progress_bar(completed: int, total: int, width: int = BAR_CELLS) -> Text:
    """`██████░░░░` — Roo's fill/empty pair, theme-coloured.

    Empty cells are a light shade rather than spaces so the bar still reads as
    a bar at a glance; spaces make it look like the bar is only half there.
    """
    colors = _colors()
    if width <= 0 or total <= 0:
        return Text("")
    filled = int(width * completed / total)
    filled = max(1, filled) if completed > 0 else 0
    filled = min(width, filled)
    # Success only once everything is done, accent while work remains: a green
    # bar at 90% overstates how finished the job is.
    color = colors.success if completed >= total and total > 0 else colors.accent
    return Text("█" * filled + "░" * (width - filled), style=Style(color=color))


def header(counter_width: int = 0) -> Text:
    """`● Todos` — the accent dot and bold word shared by every pinned strip."""
    return strip_header("Todos")


def _ellipsize(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    if width == 1:
        return "…"
    out = ""
    used = 0
    for ch in text:
        size = cell_len(ch)
        if used + size > width - 1:
            break
        out += ch
        used += size
    return out + "…"


def _prefix_cells() -> int:
    """Cells :func:`tree_prefix` spends before the subject starts."""
    return cell_len(f"{TREE_GUTTER} {TREE_EDGE} x ")


def task_row(
    task: Any,
    *,
    width: int,
    frame: int = 0,
    blocked: bool = False,
    last: bool = True,
    show_id: bool = False,
    show_description: bool = False,
) -> Text:
    """One ``│ ├─ ⠹ subject`` row.

    The running row shows ``active_form`` instead of ``subject``: a live verb
    phrase ("Writing the tests") reads as work happening, where the imperative
    subject ("Write the tests") reads as a task still waiting to be picked up.
    Every other row shows ``subject``, because the plan is what the reader is
    checking against.
    """
    colors = _colors()
    dim = Style(color=colors.dim) if colors else Style()
    muted = Style(color=colors.muted) if colors else Style()
    fg = Style(color=colors.fg) if colors else Style()
    notice = Style(color=colors.notice) if colors else Style()

    glyph, glyph_color = status_glyph(task.status, frame=frame, blocked=blocked)
    row = tree_prefix(last=last, glyph=glyph, glyph_color=glyph_color)

    id_part = f"#{task.id} " if show_id else ""
    deps = getattr(task, "blocked_by", ()) or ()
    # ``←`` rather than a chain glyph: text presentation in every terminal.
    dep_part = f" ← #{',#'.join(str(d) for d in deps)}" if deps else ""

    running = task.status == "in_progress"
    active = getattr(task, "active_form", None) if running else None
    budget = width - _prefix_cells() - cell_len(id_part) - cell_len(dep_part)

    if active:
        # The subject gets first claim on the cells and the live verb phrase
        # takes a third of what is left. Sizing the phrase first and letting the
        # subject absorb the remainder keeps the subject whole in the common
        # case, where the phrase is the longer of the two.
        active_text = str(active)
        active_width = min(cell_len(active_text), max(0, budget // 3))
        subject = _ellipsize(task.subject, max(MIN_SUBJECT_WIDTH, budget - active_width - 1))
        active_part = _ellipsize(active_text, active_width)
    else:
        subject = _ellipsize(task.subject, budget)
        active_part = ""

    if show_id:
        row.append(id_part, style=dim)

    if task.status == "completed":
        row.append(subject, style=Style(color=colors.muted if colors else None, strike=True))
    elif running:
        row.append(subject, style=Style(color=colors.fg if colors else None, bold=True))
        if active_part:
            row.append(" ")
            row.append(active_part, style=dim)
    else:
        row.append(subject, style=Style(color=colors.muted if colors else None) if blocked else fg)

    if dep_part:
        row.append(dep_part, style=notice if blocked else dim)

    if show_description and getattr(task, "description", None):
        row.append("\n")
        indent = " " * _prefix_cells()
        row.append(
            f"{indent}{_ellipsize(task.description, max(0, width - _prefix_cells()))}", style=muted
        )

    return row


def render_strip(
    tasks: Sequence[Any],
    *,
    width: int = 80,
    frame: int = 0,
    max_rows: int = MAX_ROWS,
    header_line: bool = True,
) -> Text:
    """The pinned strip: header with a progress bar, then one row per task.

    The counter and the bar live on the header row rather than in a footer, so
    a long list scrolls the tasks and not the number that says how far along
    the list is.
    """
    colors = _colors()
    tree_style = Style(color=colors.dim) if colors else Style()
    live = active_tasks(tasks)
    completed, total = counts(tasks)
    blocked = blocked_ids(tasks)

    text = Text()
    if header_line:
        head = header()
        counter = f"{completed}/{total}"
        bar = progress_bar(completed, total, BAR_CELLS)
        tail = f"{bar.plain} {counter}"
        # Pad between the word and the bar so the two never collide on a
        # narrow terminal; the bar gives up cells first.
        used = cell_len(head.plain) + cell_len(tail)
        pad = max(1, width - used)
        text.append_text(head)
        text.append(" " * pad)
        text.append(bar)
        text.append(" ")
        text.append(counter, style=Style(color=colors.muted if colors else None))
        text.append("\n")

    shown = live[:max_rows]
    hidden = len(live) - len(shown)
    for index, task in enumerate(shown):
        text.append_text(
            task_row(
                task,
                width=width,
                frame=frame,
                blocked=task.id in blocked,
                last=index == len(shown) - 1 and not hidden,
            )
        )
        text.append("\n")

    if hidden > 0:
        line = Text()
        line.append(f"{TREE_GUTTER} ", style=tree_style)
        line.append(f"{TREE_LAST} … +{hidden} more", style=tree_style)
        text.append_text(line)
        text.append("\n")

    if text.plain.endswith("\n"):
        text.right_crop()
    return text


def render_list(
    tasks: Sequence[Any], *, width: int = 80, frame: int = 0, expanded: bool = False
) -> Text:
    """The in-chat block body: the whole list, ids and details when expanded."""
    colors = _colors()
    muted = Style(color=colors.muted) if colors else Style()
    live = active_tasks(tasks)
    if not live:
        return Text("No tasks", style=muted)

    completed, total = counts(tasks)
    blocked = blocked_ids(tasks)
    text = Text()
    if expanded:
        text.append(progress_bar(completed, total, BAR_CELLS))
        text.append(f" {completed}/{total} completed", style=muted)
        text.append("\n")

    for index, task in enumerate(live):
        text.append_text(
            task_row(
                task,
                width=width,
                frame=frame,
                blocked=task.id in blocked,
                last=index == len(live) - 1,
                show_id=expanded,
                show_description=expanded,
            )
        )
        text.append("\n")

    if text.plain.endswith("\n"):
        text.right_crop()
    return text


def render_delta(
    delta: dict[str, Any], tasks: Sequence[Any], *, width: int = 80, frame: int = 0
) -> Text:
    """Only what changed since the last todo call.

    A long plan updated thirty times is thirty identical lists in the
    scrollback if every call reprints all of it. Crush solved this by drawing
    the full list exactly twice — on creation and on completion — and the
    changed row alone in between. Same rule here: the pinned strip carries the
    current state, so the block in the scrollback only has to carry the news.
    """
    colors = _colors()
    success_color = colors.success if colors else "#00ff88"

    if delta.get("is_new"):
        return render_list(tasks, width=width, frame=frame)

    if delta.get("all_completed"):
        out = render_list(tasks, width=width, frame=frame)
        out.append("\n")
        out.append("✓ all tasks complete", style=Style(color=success_color))
        return out

    live = active_tasks(tasks)
    by_id = {t.id: t for t in live}
    blocked = blocked_ids(tasks)

    # Finishes first, then additions, and the running row last: the running row
    # is where the reader's eye finishes, and it is the one that animates.
    ids: list[int] = [i for i in delta.get("just_completed") or [] if i in by_id]
    ids += [i for i in delta.get("just_added") or [] if i in by_id]
    started = delta.get("just_started")
    if started is not None and started in by_id:
        ids.append(started)

    if not ids:
        # A pure metadata or dependency edit, or a status set to the value the
        # task already had: nothing to announce.
        return Text("")

    text = Text()
    for position, task_id in enumerate(ids):
        text.append_text(
            task_row(
                by_id[task_id],
                width=width,
                frame=frame,
                blocked=task_id in blocked,
                last=position == len(ids) - 1,
            )
        )
        text.append("\n")

    text.right_crop()
    return text
