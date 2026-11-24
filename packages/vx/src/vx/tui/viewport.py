"""Transcript viewport: block cache, spacing, scroll and the visible window.

fx keeps the welcome banner pinned while newer rows scroll beneath it; the
same split is applied here, so the brand line never leaves the screen.
"""

from __future__ import annotations

from vx.tui import blocks


class Viewport:
    """Blocks, their rows, and the window the terminal shows.

    The transcript is kept whole unless a scrollback limit says otherwise: vx
    owns the scrollback (the alternate screen has none of the terminal's), so
    bounding it is a preference, the way tmux's ``history-limit`` bounds a pane.
    """

    def __init__(self) -> None:
        self.blocks: list[blocks.Block] = []
        self.scroll = 0
        #: Rows to keep; 0 keeps every block.
        self.history_rows = 0
        #: Rows the limit has dropped so far, and where its marker sits.
        self.dropped_rows = 0
        self.fold: blocks.HistoryFold | None = None
        self._width = 0
        self._rows: list[str] = []
        self._dirty = True
        self._pinned_rows = 2

    # -- content -----------------------------------------------------------

    def add(self, block: blocks.Block) -> None:
        self.blocks.append(block)
        self._dirty = True
        self.scroll = 0

    def clear(self) -> None:
        self.blocks.clear()
        self._rows = []
        self.scroll = 0
        self.dropped_rows = 0
        self.fold = None
        self._dirty = False

    def set_history_limit(self, rows: int) -> None:
        """Bound the transcript to ``rows`` of scrollback; 0 keeps everything."""
        self.history_rows = max(0, rows)
        self._dirty = True

    def touch(self) -> None:
        self._dirty = True

    def tail(self) -> blocks.Block | None:
        return self.blocks[-1] if self.blocks else None

    # -- measurement -------------------------------------------------------

    def _build(self, width: int) -> tuple[list[str], list[int]]:
        """Rows, and how many rows each block (plus its gap) took."""
        rows: list[str] = []
        heights: list[int] = []
        previous: str | None = None
        for block in self.blocks:
            block_rows = block.rows(width)
            if not block_rows:
                heights.append(0)
                continue
            gap = blocks.spacing(previous, block.kind)
            heights.append(gap + len(block_rows))
            for _ in range(gap):
                rows.append("")
            rows.extend(block_rows)
            previous = block.kind
        return rows, heights

    def _rebuild(self, width: int) -> None:
        rows, heights = self._build(width)
        if self.history_rows and len(rows) > self.history_rows:
            # The fold marker is added by the prune, so it has to fit too.
            excess = len(rows) - self.history_rows + (0 if self.fold is not None else 2)
            if self._prune(width, excess, heights):
                rows, _ = self._build(width)
        self._rows = rows
        self._width = width
        self._dirty = False

    def _first_droppable(self) -> int:
        """Index of the first block the limit may drop.

        The welcome banner and the fold marker that stands in for what was
        dropped are both part of the frame, so pruning starts after them.
        """
        index = 0
        if self.blocks and isinstance(self.blocks[0], blocks.Welcome):
            index = 1
        if self.fold is not None and index < len(self.blocks) and self.blocks[index] is self.fold:
            index += 1
        return index

    def _prune(self, width: int, excess: int, heights: list[int]) -> bool:
        """Drop the oldest blocks until ``excess`` rows are gone."""
        start = self._first_droppable()
        # The tail stays: it is where the running turn draws.
        cut = start
        removed_rows = 0
        while cut < len(self.blocks) - 1 and removed_rows < excess:
            removed_rows += heights[cut]
            cut += 1
        if cut == start:
            return False
        del self.blocks[start:cut]
        self.dropped_rows += removed_rows
        if self.fold is None:
            self.fold = blocks.HistoryFold(dropped=self.dropped_rows)
            self.blocks.insert(start, self.fold)
        else:
            self.fold.dropped = self.dropped_rows
        return True

    def lines(self, width: int) -> list[str]:
        if self._dirty or width != self._width:
            self._rebuild(width)
        return self._rows

    def total(self, width: int) -> int:
        return len(self.lines(width))

    # -- window ------------------------------------------------------------

    def visible(self, width: int, height: int) -> list[str]:
        """Exactly ``height`` rows for the transcript area.

        Content is top-aligned (fx grows downward from the banner), the last
        row stays blank as fx's idle gap above the divider, and once content
        overflows the banner stays pinned above the scrolling tail.
        """
        height = max(1, height)
        rows = self.lines(width)
        body = max(1, height - 1)  # keep one blank row as the footer gap

        if len(rows) <= body:
            window = list(rows) + [""] * (body - len(rows))
            return [*window, ""]

        pinned = min(self._pinned_rows, body - 1, len(rows))
        tail_space = max(1, body - pinned)
        self.scroll = max(0, min(self.scroll, self._max_scroll(width, height)))
        end = len(rows) - self.scroll
        start = max(pinned, end - tail_space)
        window = list(rows[:pinned]) + list(rows[start:end])
        window = window[:body]
        window += [""] * (body - len(window))
        return [*window, ""]

    # -- scrolling ---------------------------------------------------------

    def _max_scroll(self, width: int, height: int) -> int:
        """The furthest back the window goes without leaving the transcript.

        One formula for every caller: the window keeps the pinned rows and
        fills the rest, so a bound computed differently would either hide the
        first rows or let the view drift into blank space.
        """
        body = max(1, height - 1)
        tail_space = max(1, body - self._pinned_rows)
        return max(0, len(self.lines(width)) - self._pinned_rows - tail_space)

    def scroll_by(self, delta: int, width: int, height: int) -> None:
        self.scroll = max(0, min(self._max_scroll(width, height), self.scroll + delta))

    def page(self, direction: int, width: int, height: int) -> None:
        self.scroll_by(direction * max(1, height - 2), width, height)
