"""The pinned ``Agents`` panel.

Sub-agents used to be visible only as the tool block for the dispatch that
launched them: one row in the scrollback, frozen at "0 turns · 0 tool calls"
for a background run, while a dozen other sub-agents worked invisibly. This
module is the standing view — a panel pinned above the editor that lists every
sub-agent the session has in flight, what each one is doing right now, and how
many are still waiting for a slot.

It is pinned, not appended: the rows live in a fixed strip under the status
line, so the numbers keep updating while the chat scrolls behind them.

Layout (a plain tree, no chrome)::

    ● Agents
    │ ├─ ⠼ Rate the VTX codebase   subagent · 35 tools · 859.2k tokens · 2m15s
    │ │  grep · reading src/vtx/ai/agent/loop.py
    │ ├─ ⠹ Find the auth bug        reviewer · 1 tool · 1.2k tokens · 12.3s
    │ │  thinking…
    │ ○ 12 queued
    │ ✓ 2 finished · 4.1k tokens

Three rules earn their keep here:

- A row leads with the *task* ("Rate the VTX codebase"), because that is what
  the run is recognised by. The agent profile's blurb is boilerplate — for the
  default sub-agent every row would read "the parent's tools and instructions".
- Finished runs collapse into one summary line. A row per dead agent is noise,
  and the strip should shrink back to nothing once the work is done.
- Queued agents get a count, not rows: they are waiting, so there is nothing to
  report about them, and a screen full of "queued" would crowd out the agents
  actually working.

Rendering is a pure function of the registry, so the same rows serve the panel,
the goal beacon, and tests.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from rich.cells import cell_len
from rich.style import Style
from rich.text import Text
from textual.timer import Timer
from textual.widgets import Static

from vtx.agent.tools.task import DEFAULT_SUBAGENT
from vtx.coding_agent.tui.goal_agents import DONE_LINGER_SECONDS, REGISTRY, SubagentRun
from vtx.core.config import config
from vtx.tui.task_ui import SPINNER, describe_activity, format_tokens

#: Rows shown before the rest collapse into a ``+N more`` line. The panel is a
#: strip above the editor, not a log, so it must not eat the input box.
MAX_ROWS = 5

#: Longest task label the row will show before ellipsizing.
MAX_TITLE_WIDTH = 34

#: Narrowest task label worth showing before other segments start losing cells.
MIN_LABEL_WIDTH = 10

#: Longest live activity (tool call or streaming response) a row will show.
MAX_ACTIVITY_WIDTH = 46

#: Below this, an activity is noise ("think…") and the cells are better spent
#: on the label and the counters.
MIN_ACTIVITY_WIDTH = 9


#: Agent name suffix is dropped past this length — it is secondary detail.
MAX_AGENT_WIDTH = 16

#: Spin cadence for running rows. 8fps reads as "alive" without flicker.
TICK_SECONDS = 0.12

#: Repaint cadence while nothing is running but the panel is still on screen
#: (the linger window). Nothing animates here; it only has to notice when the
#: window closes.
LINGER_TICK_SECONDS = 0.5

#: The grammar every strip above the editor is built from. These are public
#: because the goal beacon is drawn from the same vocabulary: one `● Header`,
#: one `│ ├─` tree, bold labels, dim activity, muted counters. A second strip
#: with its own shape directly below the first reads as two unrelated widgets
#: rather than as one panel.
TREE_EDGE = "├─"
TREE_LAST = "└─"
TREE_GUTTER = "│"
HEADER_DOT = "●"


def strip_header(label: str) -> Text:
    """`● Agents` — the accent dot and bold word that head a pinned strip.

    The dot carries the accent, the word carries the weight. A shaded chip
    behind the word (` vtx-goal `) reads heavier and pulls the eye to the
    least important part of the row: the strip's own name.
    """
    colors = config.ui.colors
    out = Text()
    out.append(f"{HEADER_DOT} ", style=Style(color=colors.accent))
    out.append(label, style=Style(color=colors.fg, bold=True))
    return out


def tree_prefix(*, last: bool, glyph: str, glyph_color: str, depth: int = 0) -> Text:
    """`│ ├─ ⠋ ` — the vertical, the branch and the status glyph, in one span.

    Split across three styles so the gutter stays a hairline, the branch reads
    as structure, and the glyph reads as state. Every strip above the editor
    starts its rows with this, so a reader moving between them is reading one
    panel and not two.

    ``depth`` nests a row under another: the extra ``│ `` bars go *before* the
    branch, so a subtask reads as hanging under its parent
    (``│ │ ├─ · t2.2``) rather than as a sibling with a stray bar after it.
    """
    colors = config.ui.colors
    branch = TREE_LAST if last else TREE_EDGE
    out = Text()
    out.append(TREE_GUTTER, style=Style(color=colors.dim))
    out.append(" │" * depth, style=Style(color=colors.dim))
    out.append(f" {branch} ", style=Style(color=colors.dim))
    out.append(glyph, style=Style(color=glyph_color, bold=True))
    out.append(" ")
    return out


def tree_prefix_cells(depth: int = 0) -> int:
    """Cells :func:`tree_prefix` occupies, so columns can be sized off it."""
    return cell_len(f"{TREE_GUTTER} {'│' * depth} {TREE_EDGE} x ")


def _agent_tag(run: SubagentRun) -> str:
    """The agent name, or ``""`` when it identifies nothing.

    The default sub-agent is literally named ``subagent``, which on every row
    of a default fan-out is a word that tells the reader nothing.
    """
    return "" if run.name == DEFAULT_SUBAGENT else run.name


def _ellipsize(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


def _stream_tail(text: str, limit: int) -> str:
    """The newest words of an in-flight response, trimmed to ``limit`` cells.

    Left-truncated, not right: while a response streams in, the characters
    that just arrived are the ones being read, so they have to stay on screen
    instead of being pushed off the end by the paragraph before them.
    """
    line = next((part.strip() for part in reversed(text.splitlines()) if part.strip()), "")
    if len(line) > limit:
        return "…" + line[-(limit - 1) :]
    return line


def status_glyph(run: SubagentRun, frame: int) -> tuple[str, str]:
    """``(glyph, color)`` for one run's row marker."""
    colors = config.ui.colors
    if run.running:
        return SPINNER[frame % len(SPINNER)], colors.accent
    if run.queued:
        return "○", colors.notice
    if run.status == "ok":
        return "✓", colors.success
    if run.status == "stopped":
        return "■", colors.dim
    return "✗", colors.failed


def _duration(ms: float) -> str:
    """``2m15s`` past a minute, so a long fan-out does not print 859.1s."""
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"
    return f"{int(seconds // 3600)}h{int((seconds % 3600) // 60):02d}m"


def metrics_for(run: SubagentRun) -> str:
    """`·`-separated counters, or ``""`` for a run that has produced none."""
    if run.queued or run.started_at is None:
        return ""
    parts: list[str] = []
    tool_uses = run.tool_uses()
    if tool_uses:
        parts.append(f"{tool_uses} tool{'' if tool_uses == 1 else 's'}")
    parts.append(format_tokens(run.tokens))
    parts.append(_duration(run.elapsed_ms))
    return " · ".join(parts)


def activity_for(run: SubagentRun, limit: int = MAX_ACTIVITY_WIDTH) -> str:
    """What this sub-agent is doing right now, on its own row.

    One line covers all three phases, in the order they happen: a tool call
    while one is in flight, the response streaming in once the model starts
    writing, and plain "thinking…" before either. It repaints in place, so
    watching a row is watching the sub-agent.
    """
    if run.queued:
        if run.queue_position > 0:
            return f"queued · {run.queue_position} ahead of the cap"
        return "queued"
    if run.error:
        return _ellipsize(run.error, limit)
    if run.active_tool:
        # Name the tool as well as the action — "grep · searching…" locates a
        # stuck agent in a way "searching…" alone does not.
        return _ellipsize(f"{run.active_tool} · {describe_activity(run.active_tool, '')}", limit)
    if run.last_text:
        return _ellipsize(_stream_tail(run.last_text, limit), limit)
    if run.finished:
        return run.top_tools() or "done"
    return "thinking…"


def visible_runs(runs: Sequence[SubagentRun]) -> list[SubagentRun]:
    """Rows to draw: running agents, oldest dispatch first.

    Queued agents are absent (the count line carries them) and so are finished
    ones (the summary line carries those).
    """
    return sorted((r for r in runs if r.running), key=lambda r: r.dispatched_at)


def finished_summary(runs: Sequence[SubagentRun]) -> str:
    """`✓ 2 finished · 4.1k tokens` for the runs that already landed."""
    done = [r for r in runs if r.finished]
    if not done:
        return ""
    colors = config.ui.colors
    failed = sum(1 for r in done if r.status == "error")
    text = Text()
    text.append(f"{TREE_GUTTER} ", style=Style(color=colors.dim))
    glyph, color = ("✗", colors.failed) if failed else ("✓", colors.success)
    text.append(f"{glyph} ", style=Style(color=color))
    count = f"{len(done)} finished"
    text.append(count, style=Style(color=colors.dim if not failed else colors.failed))
    if failed:
        text.append(f" · {failed} failed", style=Style(color=colors.failed))
    tokens = sum(r.tokens for r in done)
    if tokens:
        text.append(f" · {format_tokens(tokens)}", style=Style(color=colors.muted))
    return text.plain


def should_show(runs: Sequence[SubagentRun], *, now: float | None = None) -> bool:
    """Whether the panel earns its strip right now.

    Hidden when nothing is in flight. Once the last sub-agent lands it lingers
    briefly so its summary line is actually readable, then goes away.
    """
    now = time.monotonic() if now is None else now
    if any(not run.finished for run in runs):
        return True
    ended = [run.ended_at for run in runs if run.ended_at is not None]
    return bool(ended) and (now - max(ended)) < DONE_LINGER_SECONDS


def _fit_metrics(metrics: str, budget: int) -> str:
    """Drop leading ``·``-separated counters until ``metrics`` fits ``budget``.

    The duration is kept longest: "is it stuck?" is the question a watcher
    actually has, and the tool count is the first thing worth losing.
    """
    if budget <= 0:
        return ""
    parts = metrics.split(" · ")
    while len(parts) > 1 and len(" · ".join(parts)) > budget:
        parts.pop(0)
    return " · ".join(parts) if len(" · ".join(parts)) <= budget else ""


def _last_field(fields: tuple[str, str, str, str]) -> int:
    """Index of the right-most non-empty field in a row."""
    for index in range(len(fields) - 1, -1, -1):
        if fields[index]:
            return index
    return 0


def _cell(text: str, width: int) -> str:
    """Fit ``text`` into exactly ``width`` cells, padded with spaces.

    ``str.ljust`` pads but never truncates, so a column budget of zero would
    still print the whole field — which is how a row ends up wider than the
    terminal it was measured against.
    """
    if width <= 0:
        return ""
    return _ellipsize(text, width).ljust(width)


def _shrink_metrics(metrics: str, budget: int) -> str:
    """Longest run of ``·``-separated counters that fits ``budget`` cells.

    Counters are dropped whole, never truncated mid-number into something like
    ``35 to…``. The tool count goes first, then the token count; the duration
    is kept longest, because "is it stuck?" is the question a watcher actually
    has.
    """
    parts = metrics.split(" · ") if metrics else []
    while parts and len(" · ".join(parts)) > budget:
        parts.pop(0)
    return " · ".join(parts)


def _layout_columns(runs: Sequence[SubagentRun], budget: int) -> list[tuple[str, str, str, str]]:
    """Lay out every row as fixed columns with the activity as the flex one.

    Fixed columns are what make the counters scannable: the eye finds the
    token column because it is in the same place on every line. Only the
    activity flexes, because it is the part whose length is genuinely
    unpredictable — a tool name, a thinking placeholder, or a paragraph of
    response streaming in.

    When the terminal cannot hold the columns, they are given up in a fixed
    order — agent name, then counters, then label width — and the activity
    absorbs whatever is left.
    """
    labels = [_ellipsize(r.title, MAX_TITLE_WIDTH) for r in runs]
    tags = [_agent_tag(r) for r in runs]
    metrics = [metrics_for(r) for r in runs]

    def gaps(columns: int) -> int:
        return 2 * max(0, columns - 1)

    label_width = min(MAX_TITLE_WIDTH, max((len(x) for x in labels), default=0))
    tag_width = min(MAX_AGENT_WIDTH, max((len(x) for x in tags), default=0))
    metrics = [_shrink_metrics(m, MAX_TITLE_WIDTH + MAX_ACTIVITY_WIDTH) for m in metrics]
    metrics_width = max((len(m) for m in metrics), default=0)

    if label_width + tag_width + metrics_width + gaps(4) > budget:
        tag_width = 0
    # Shrink the counters a part at a time, stopping the moment nothing gets
    # shorter. (Testing the joined string for " · " looks like it works and
    # does not: two empty metrics still join to " · ".)
    while label_width + metrics_width + gaps(3) > budget:
        wider = metrics_width
        metrics = [_shrink_metrics(m, max(0, wider - 1)) for m in metrics]
        metrics_width = max((len(m) for m in metrics), default=0)
        if metrics_width >= wider:
            break
    if label_width + metrics_width + gaps(3) > budget:
        metrics = ["" for _ in metrics]
        metrics_width = 0
    if label_width + metrics_width + gaps(2) > budget:
        # MIN_LABEL_WIDTH is a preference for readable terminals, not a
        # guarantee: on a very narrow one the label gives up cells instead of
        # pushing the panel wider than the screen.
        label_width = max(0, min(label_width, budget - metrics_width - gaps(2)))

    activity_width = budget - label_width - tag_width - metrics_width - gaps(4)
    activity_width = max(0, min(MAX_ACTIVITY_WIDTH, activity_width))
    if activity_width < MIN_ACTIVITY_WIDTH:
        # A one-cell activity is just an ellipsis; better to spend the cells on
        # the label and the counters.
        activity_width = 0

    rows: list[tuple[str, str, str, str]] = []
    for index, run in enumerate(runs):
        activity = _ellipsize(activity_for(run, activity_width), activity_width)
        rows.append(
            (
                _cell(labels[index], label_width),
                _cell(tags[index], tag_width),
                _cell(activity, activity_width),
                _cell(metrics[index], metrics_width),
            )
        )
    return rows


def render_agents(
    runs: Sequence[SubagentRun],
    *,
    width: int = 80,
    frame: int = 0,
    max_rows: int = MAX_ROWS,
    header: bool = True,
) -> Text:
    """Draw the panel: one streaming line per running agent, then the counts."""
    colors = config.ui.colors
    tree_style = Style(color=colors.dim)
    styles = {
        "label": Style(color=colors.fg, bold=True),
        "activity": Style(color=colors.dim),
        "metrics": Style(color=colors.muted),
        "tag": Style(color=colors.muted),
    }
    rows = visible_runs(runs)
    shown = rows[:max_rows]
    hidden = len(rows) - len(shown)

    # "│ ├─ ⠼ "
    prefix_cells = cell_len(f"{TREE_GUTTER} {TREE_EDGE} x ")
    # No floor: on a terminal narrower than the prefix the row has to shrink
    # below it rather than push the panel wider than the screen.
    budget = max(0, width - prefix_cells)
    layout = _layout_columns(shown, budget)

    text = Text()
    if header:
        text.append_text(strip_header("Agents"))
        text.append("\n")

    for index, run in enumerate(shown):
        glyph, glyph_color = status_glyph(run, frame)
        row = Text()
        row.append_text(
            tree_prefix(
                last=index == len(shown) - 1 and not hidden, glyph=glyph, glyph_color=glyph_color
            )
        )

        label, tag, activity, metrics = layout[index]
        # Every field is emitted, padding included, so the counters land in the
        # same column on every row even when one agent has no activity to show.
        for position, (key, field_text) in enumerate(
            (("label", label), ("tag", tag), ("activity", activity), ("metrics", metrics))
        ):
            if not field_text:
                continue
            if len(row.plain) > prefix_cells:
                row.append("  ")
            # A field padded past the last one on screen would leave the line
            # with trailing blanks, so the tail is trimmed after the fact.
            last_visible = position == _last_field((label, tag, activity, metrics))
            row.append(field_text.rstrip() if last_visible else field_text, style=styles[key])
        text.append(row)
        text.append("\n")

    footer_gutter_cells = cell_len(TREE_GUTTER) + 1

    def footer(body: str, body_style: Style) -> None:
        line = Text()
        line.append(f"{TREE_GUTTER} ", style=tree_style)
        line.append(_ellipsize(body, max(0, width - footer_gutter_cells)), style=body_style)
        text.append(line)
        text.append("\n")

    if hidden > 0:
        footer(f"└─ … +{hidden} more running", tree_style)

    queued_total = sum(1 for r in runs if r.queued)
    if queued_total:
        footer(f"○ {queued_total} queued", Style(color=colors.muted))

    summary = finished_summary(runs)
    if summary:
        text.append(Text(_ellipsize(summary, max(0, width - footer_gutter_cells))))

    if text.plain.endswith("\n"):
        text.right_crop()
    return text


class AgentsPanel(Static):
    """Pinned strip listing every sub-agent the session has in flight."""

    DEFAULT_CSS = """
    AgentsPanel {
        display: none;
        height: auto;
        padding: 0 1;
    }
    AgentsPanel.-visible {
        display: block;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._frame = 0
        self._timer: Timer | None = None
        self._timer_interval = 0.0
        self._last_counts: tuple[int, int] = (0, 0)

    def on_mount(self) -> None:
        self._start_timer(TICK_SECONDS)
        self.refresh_panel()

    def on_unmount(self) -> None:
        self._stop_timer()

    def _start_timer(self, interval: float) -> None:
        # Textual's Timer has no public re-interval, so swapping the timer is
        # how the cadence changes.
        if self._timer is not None:
            if self._timer_interval == interval:
                return
            self._stop_timer()
        self._timer_interval = interval
        self._timer = self.set_interval(interval, self._on_tick)

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
            self._timer_interval = 0.0

    def _on_tick(self) -> None:
        runs = REGISTRY.runs()
        running = any(run.running for run in runs)
        if running:
            self._start_timer(TICK_SECONDS)
        elif should_show(runs):
            # Linger window: nothing animates, but the strip has to notice
            # when the window closes. Without this the last finished row would
            # sit there forever, because the fast timer stops with the spinner.
            self._start_timer(LINGER_TICK_SECONDS)
        else:
            self._stop_timer()
        self._frame += 1
        self.refresh_panel()

    @property
    def counts(self) -> tuple[int, int]:
        """``(running, queued)`` for the status line."""
        return self._last_counts

    def refresh_panel(self) -> None:
        """Re-read the registry and repaint (or hide) the strip."""
        runs = REGISTRY.runs()
        running, queued, _ = REGISTRY.counts()
        self._last_counts = (running, queued)
        if not should_show(runs):
            self.remove_class("-visible")
            self.update(Text(""))
            return
        if not self.has_class("-visible"):
            self.add_class("-visible")
            self._start_timer(TICK_SECONDS if running else LINGER_TICK_SECONDS)
        self.update(render_agents(runs, width=self._content_width(), frame=self._frame))

    def _content_width(self) -> int:
        for source in (self.size.width, self.content_size.width, self.container_size.width):
            if source:
                # DEFAULT_CSS applies ``padding: 0 1`` -> two cells of chrome.
                return max(10, source - 2)
        return 80
