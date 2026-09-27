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
    │ ├─ Explore  Find TODO/FIXME comments · 0 tokens · 1.7s
    │ │  thinking…
    │ ├─ Explore  Count files and LOC · 1.7s
    │ │  reading src/vtx/tui/app.py
    │ └─ researcher  · 1.2k tokens · 4.5s
    │ ○ 28 queued

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

from vtx.ai.config import config
from vtx.tui.goal_agents import DONE_LINGER_SECONDS, REGISTRY, SubagentRun
from vtx.tui.task_ui import SPINNER, describe_activity, format_ms, format_tokens

#: Rows shown before the rest collapse into a ``+N more`` line. The panel is a
#: strip above the editor, not a log, so it must not eat the input box.
MAX_ROWS = 6

#: Longest sub-agent name the name column will reserve.
MAX_NAME_WIDTH = 14

#: Tick rate for the running spinners. 8fps reads as "alive" without flicker.
TICK_SECONDS = 0.12

_TREE_EDGE = "├─"
_TREE_LAST = "└─"
_GUTTER = "│"
_BRANCH_PAD = "│ │  "


def _ellipsize(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


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


def metrics_for(run: SubagentRun) -> str:
    """`·`-separated counters, or ``""`` for a run that has produced none."""
    if run.queued or run.started_at is None:
        return ""
    parts: list[str] = []
    tool_uses = run.tool_uses()
    if tool_uses:
        parts.append(f"{tool_uses} tool use{'s' if tool_uses != 1 else ''}")
    parts.append(format_tokens(run.tokens))
    parts.append(format_ms(run.elapsed_ms))
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
    return describe_activity(run.active_tool, run.last_text)


def _text_snippet(text: str, limit: int = 60) -> str:
    """First non-blank line of ``text``, trimmed to ``limit`` cells."""
    snippet = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if len(snippet) > limit:
        return snippet[:limit] + "…"
    return snippet


def visible_runs(runs: Sequence[SubagentRun]) -> list[SubagentRun]:
    """Rows to draw, in priority order: running, then newest finished.

    Queued sub-agents are deliberately absent: they are waiting, so there is
    nothing to report about them, and a screen full of identical "queued" rows
    would crowd out the agents actually working. The ``○ N queued`` summary
    carries that count instead.
    """
    running = sorted((r for r in runs if r.running), key=lambda r: r.dispatched_at)
    done = sorted(
        (r for r in runs if r.finished), key=lambda r: r.ended_at or r.dispatched_at, reverse=True
    )
    return running + done


def should_show(runs: Sequence[SubagentRun], *, now: float | None = None) -> bool:
    """Whether the panel earns its strip right now.

    Hidden when there is nothing in flight. Once the last sub-agent lands it
    lingers briefly so its final row is actually readable, then goes away.
    """
    now = time.monotonic() if now is None else now
    if any(not run.finished for run in runs):
        return True
    ended = [run.ended_at for run in runs if run.ended_at is not None]
    return bool(ended) and (now - max(ended)) < DONE_LINGER_SECONDS


def render_agents(
    runs: Sequence[SubagentRun],
    *,
    width: int = 80,
    frame: int = 0,
    max_rows: int = MAX_ROWS,
    header: bool = True,
) -> Text:
    """Draw the panel body: one row per sub-agent plus a queued summary."""
    colors = config.ui.colors
    tree_style = Style(color=colors.dim)
    rows = visible_runs(runs)
    shown = rows[:max_rows]
    hidden = len(rows) - len(shown)

    name_width = min(MAX_NAME_WIDTH, max((len(r.name) for r in shown), default=0))
    metric_texts = [metrics_for(r) for r in shown]
    metrics_width = max((len(m) for m in metric_texts), default=0)

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
        name = _ellipsize(run.name, name_width)
        row.append(name.ljust(name_width), style=Style(color=colors.fg, bold=True))
        row.append("  ")

        used = name_width + 7
        room = max(4, width - used - metrics_width - 3)
        description = _ellipsize(run.description, room) if run.description else ""
        if description:
            row.append(description, style=Style(color=colors.dim))
        padding = max(1, room - len(description) + 1)
        if metrics:
            row.append(" " * padding, style=Style(color=colors.dim))
            row.append(metrics, style=Style(color=colors.muted))
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
        more.append(f"… +{hidden} more agent{'s' if hidden != 1 else ''}", style=tree_style)
        text.append(more)
        text.append("\n")

    queued_total = sum(1 for r in runs if r.queued)
    if queued_total:
        queue_line = Text()
        queue_line.append(f"{_GUTTER} ", style=tree_style)
        queue_line.append(f"○ {queued_total} queued", style=Style(color=colors.muted))
        text.append(queue_line)

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
        self._last_counts: tuple[int, int] = (0, 0)

    def on_mount(self) -> None:
        self._timer = self.set_interval(TICK_SECONDS, self._on_tick)
        self.refresh_panel()

    def on_unmount(self) -> None:
        self._stop_timer()

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _on_tick(self) -> None:
        # Only animate while a sub-agent is actually running. A queued-only or
        # finished panel has nothing to animate, and its content only changes
        # when a progress event arrives — which restarts this timer.
        if not any(run.running for run in REGISTRY.runs()):
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
            if self._timer is None:
                self._timer = self.set_interval(TICK_SECONDS, self._on_tick)
        self.update(render_agents(runs, width=self._content_width(), frame=self._frame))

    def _content_width(self) -> int:
        for source in (self.size.width, self.content_size.width, self.container_size.width):
            if source:
                # DEFAULT_CSS applies ``padding: 0 1`` -> two cells of chrome.
                return max(10, source - 2)
        return 80
