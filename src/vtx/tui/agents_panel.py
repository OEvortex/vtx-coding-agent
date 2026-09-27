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

from rich.style import Style
from rich.text import Text
from textual.timer import Timer
from textual.widgets import Static

from vtx.ai.agent.tools.task import DEFAULT_SUBAGENT
from vtx.ai.config import config
from vtx.tui.goal_agents import DONE_LINGER_SECONDS, REGISTRY, SubagentRun
from vtx.tui.task_ui import SPINNER, describe_activity, format_tokens

#: Rows shown before the rest collapse into a ``+N more`` line. The panel is a
#: strip above the editor, not a log, so it must not eat the input box.
MAX_ROWS = 5

#: Longest task label the row will show before ellipsizing.
MAX_TITLE_WIDTH = 34

#: Narrowest task label worth showing before the metrics start losing cells.
MIN_LABEL_WIDTH = 10

#: Agent name suffix is dropped past this length — it is secondary detail.
MAX_AGENT_WIDTH = 16

#: Spin cadence for running rows. 8fps reads as "alive" without flicker.
TICK_SECONDS = 0.12

#: Repaint cadence while nothing is running but the panel is still on screen
#: (the linger window). Nothing animates here; it only has to notice when the
#: window closes.
LINGER_TICK_SECONDS = 0.5

_TREE_EDGE = "├─"
_TREE_LAST = "└─"
_GUTTER = "│"
_BRANCH_PAD = "│ │  "


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


def _text_snippet(text: str, limit: int = 60) -> str:
    """First non-blank line of ``text``, trimmed to ``limit`` cells."""
    snippet = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if len(snippet) > limit:
        return snippet[:limit] + "…"
    return snippet


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


def activity_for(run: SubagentRun) -> str:
    """The live sub-line: what this sub-agent is doing, or what it last said."""
    if run.queued:
        if run.queue_position > 0:
            return f"queued · {run.queue_position} ahead of the cap"
        return "queued"
    if run.error:
        return run.error
    if run.finished:
        # A finished agent's last words beat "done": they say what it
        # actually concluded.
        return _text_snippet(run.last_text) or run.top_tools() or "done"
    activity = describe_activity(run.active_tool, run.last_text)
    # Name the tool as well as the action — "grep · searching" locates the
    # agent in a way "searching…" alone does not.
    if run.active_tool and not activity.startswith(run.active_tool):
        return f"{run.active_tool} · {activity}"
    return activity


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
    text.append(f"{_GUTTER} ", style=Style(color=colors.dim))
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


def render_agents(
    runs: Sequence[SubagentRun],
    *,
    width: int = 80,
    frame: int = 0,
    max_rows: int = MAX_ROWS,
    header: bool = True,
) -> Text:
    """Draw the panel: a row per running agent, then queued and done counts."""
    colors = config.ui.colors
    tree_style = Style(color=colors.dim)
    rows = visible_runs(runs)
    shown = rows[:max_rows]
    hidden = len(rows) - len(shown)

    metric_texts = [metrics_for(r) for r in shown]
    metrics_width = max((len(m) for m in metric_texts), default=0)

    # Room is spent in priority order: task label first, then the agent name,
    # then the fixed-width metrics. On a narrow terminal the metrics are what
    # gives up cells, and if even a short label will not fit the name goes.
    gutter = 7  # "│ ├─ ⠼ "
    available = max(12, width - gutter)
    label_width = available - metrics_width - 2
    if label_width < MIN_LABEL_WIDTH:
        metric_texts = [_fit_metrics(m, available - MIN_LABEL_WIDTH - 2) for m in metric_texts]
        metrics_width = max((len(m) for m in metric_texts), default=0)
        label_width = max(MIN_LABEL_WIDTH, available - metrics_width - 2)

    tags = [_agent_tag(r) for r in shown]
    agent_width = min(MAX_AGENT_WIDTH, max((len(t) for t in tags), default=0))
    if agent_width and label_width < MIN_LABEL_WIDTH + agent_width + 2:
        agent_width = 0
    title_width = min(MAX_TITLE_WIDTH, label_width - (agent_width + 2 if agent_width else 0))

    text = Text()
    if header:
        text.append("● ", style=Style(color=colors.accent))
        text.append("Agents", style=Style(color=colors.fg, bold=True))
        text.append("\n")

    for index, run in enumerate(shown):
        branch = _TREE_LAST if index == len(shown) - 1 and not hidden else _TREE_EDGE
        glyph, glyph_color = status_glyph(run, frame)
        metrics = metric_texts[index]

        row = Text()
        row.append(f"{_GUTTER} {branch} ", style=tree_style)
        row.append(f"{glyph} ", style=Style(color=glyph_color))

        title = _ellipsize(run.title, title_width)
        row.append(title, style=Style(color=colors.fg, bold=True))
        tag = tags[index]
        if agent_width:
            # Pad to a fixed width so the metrics column lines up down the
            # panel, whatever the mix of agent names in the fan-out.
            row.append(" " * max(1, title_width - len(title) + 2))
            if tag and len(tag) <= agent_width:
                row.append(_ellipsize(tag, agent_width), style=Style(color=colors.muted))
            else:
                row.append(" " * agent_width)
        if metrics:
            row.append("  " + metrics, style=Style(color=colors.muted))
        text.append(row)
        text.append("\n")

        detail = Text()
        detail.append(_BRANCH_PAD, style=tree_style)
        detail.append(
            _ellipsize(activity_for(run), max(4, width - len(_BRANCH_PAD))),
            style=Style(color=colors.dim if not run.error else colors.failed),
        )
        text.append(detail)
        text.append("\n")

    if hidden > 0:
        more = Text()
        more.append(f"{_GUTTER} ", style=tree_style)
        more.append(f"└─ … +{hidden} more running", style=tree_style)
        text.append(more)
        text.append("\n")

    queued_total = sum(1 for r in runs if r.queued)
    if queued_total:
        queue_line = Text()
        queue_line.append(f"{_GUTTER} ", style=tree_style)
        queue_line.append(f"○ {queued_total} queued", style=Style(color=colors.muted))
        text.append(queue_line)
        text.append("\n")

    summary = finished_summary(runs)
    if summary:
        text.append(Text(summary))

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
