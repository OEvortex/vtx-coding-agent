"""The terminal drawer: one job's output, over the transcript, out of the way.

A running command is not history. It opens as a drawer anchored above the
composer -- the transcript keeps its rows, the panel keeps its list, and another
terminal is one click away. The drawer reads the job the registry owns, so the
sidebar row, the state line and the output cannot disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vx.tui import ansi, theme

#: Small enough for a 24-row window, tall enough for a boot log.
MIN_HEIGHT = 5
MAX_HEIGHT = 12
#: Lines of output the drawer wants when it is not full.
WANTED_BODY = 10


def _strip(text: str) -> str:
    return ansi.strip_ansi(text)


def _ruled(corner: str, left: str, right: str, width: int) -> str:
    """``╭─ title ───── state ─╮``: the rule takes whatever the ends leave.

    The two ends are clipped rather than allowed to push the corner off the
    row, so a long command or a long key list costs its own text, never the box.
    """
    t = theme.current()
    # ╭─ + left + ─ + right + ─╮ costs eight cells of chrome, then the rule.
    room = max(0, width - 8)
    right = ansi.clip_ellipsis(right, max(0, room // 2))
    left = ansi.clip_ellipsis(left, max(0, room - ansi.width(right)))
    rule = "─" * max(0, room - ansi.width(left) - ansi.width(right))
    end = "─╮" if corner.startswith("╭") else "─╯"
    return (
        f"{t.dim_style}{corner}{ansi.RESET}{t.hint_style}{left}{ansi.RESET}"
        f"{t.dim_style} {rule} {ansi.RESET}{t.dim_style}{right} {ansi.RESET}"
        f"{t.dim_style}{end}{ansi.RESET}"
    )


@dataclass
class TerminalPanel:
    """One job, drawn as a box: title, output, and the way out."""

    job: Any
    scroll: int = 0
    #: A running job should show what it said last, not what it said first.
    #: Scrolling turns this off; jumping to the end turns it back on.
    follow: bool = True

    # -- the box's own geometry -------------------------------------------

    @staticmethod
    def height(rows: int) -> int:
        """The drawer's height for a window of ``rows`` -- never the whole window."""
        return max(MIN_HEIGHT, min(MAX_HEIGHT, min(WANTED_BODY + 2, max(0, rows - 3))))

    @staticmethod
    def width(cols: int) -> int:
        return max(20, min(cols - 2, 88))

    def body(self) -> list[str]:
        # ``rows`` asks for the body, then ``first_line`` asks again; the strip
        # is the expensive half, so it is paid once per batch of output. ``total``
        # moves on every appended line, which makes it a safe freshness key.
        total = getattr(self.job, "total", -1)
        cached = self.__dict__.get("_body_cache")
        if cached is not None and cached[0] == total:
            return cached[1]
        lines = [_strip(line) for line in self.job.tail(2000)]
        self._body_cache = (total, lines)
        return lines

    def first_line(self, body_h: int) -> int:
        """Where the visible window starts: the tail while following."""
        total = len(self.body())
        newest = max(0, total - body_h)
        return newest if self.follow else max(0, min(self.scroll, newest))

    def max_scroll(self) -> int:
        return max(0, len(self.body()) - 1)

    # -- what the title says ----------------------------------------------

    def state_text(self) -> str:
        if self.job.status == "running":
            return f"running · {self.job.label()}"
        return self.job.state_text()

    def state_style(self) -> str:
        t = theme.current()
        return {
            "running": t.tag_style,
            "done": t.dim_style,
            "failed": t.red_style,
            "stopped": t.red_style,
        }.get(self.job.status, t.dim_style)

    # -- the rows ----------------------------------------------------------

    def rows(self, width: int, height: int) -> list[str]:
        t = theme.current()
        body_h = max(1, height - 2)
        lines = self.body()
        total = len(lines)
        first = self.first_line(body_h)
        shown = lines[first : first + body_h]
        while len(shown) < body_h:
            shown.append("")

        title = f"{self.job.id} · {self.job.command}"
        state = self.state_text()
        head = _ruled("╭─ ", title, state, width)
        counter = f"{first + 1}-{min(first + body_h, total)}/{total}" if total else "0 lines"
        scrolling = "↑↓ scroll"
        keys = (
            f"{scrolling} · k kill · esc close"
            if self.job.status == "running"
            else f"{scrolling} · esc close"
        )
        foot = _ruled("╰─ ", keys, counter, width)

        out = [head]
        for line in shown:
            text = ansi.clip_ellipsis(f" {line}", width - 2)
            out.append(
                f"{t.dim_style}│{ansi.RESET}{ansi.pad(text, width - 2)}{t.dim_style}│{ansi.RESET}"
            )
        out.append(foot)
        return [ansi.clip(row, width) for row in out]
