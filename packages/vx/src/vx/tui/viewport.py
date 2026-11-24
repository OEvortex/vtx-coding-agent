"""Transcript viewport: block cache, spacing, scroll and the visible window.

fx keeps the welcome banner pinned while newer rows scroll beneath it; the
same split is applied here, so the brand line never leaves the screen.
"""

from __future__ import annotations

from vx.tui import ansi, blocks


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
        #: ``scroll == 0`` means the tail is on screen. Scrolling up sets this
        #: false; new rows then count up instead of yanking the window down.
        self.follow = True
        self.unseen = 0
        #: Incremental search (ctrl+f): (row, start, end) spans into the rows.
        self.search = ""
        self.search_matches: list[tuple[int, int, int]] = []
        self.search_index = 0
        #: Full-transcript row behind each visible row, for highlighting.
        self._window: list[int | None] = []

    # -- content -----------------------------------------------------------

    def add(self, block: blocks.Block) -> None:
        """Append a block, keeping the reader's place if they scrolled up.

        Resetting the scroll here would yank the view back to the tail every
        time a token or a tool call arrived, which makes reading scrollback
        impossible while a turn is running. ``_rebuild`` moves the anchor by
        the new rows instead, so the visible text does not shift.
        """
        self.blocks.append(block)
        self._dirty = True

    def clear(self) -> None:
        self.blocks.clear()
        self._rows = []
        self.scroll = 0
        self.dropped_rows = 0
        self.fold = None
        self._dirty = False
        self.follow = True
        self.unseen = 0
        self.search = ""
        self.search_matches = []
        self.search_index = 0
        self._window = []

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
        grown = len(rows) - len(self._rows)
        if not self.follow:
            # Hold the reader's place: rows appended below the window would
            # otherwise slide the text they are looking at out from under them.
            self.scroll = max(0, self.scroll + grown)
            self.unseen += max(0, grown)
        self._rows = rows
        self._width = width
        self._dirty = False
        self._recompute_matches()

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

    # -- search ------------------------------------------------------------

    def _recompute_matches(self) -> None:
        query = self.search.strip()
        self.search_matches = []
        if query:
            needle = query.lower()
            for row_index, row in enumerate(self._rows):
                plain = ansi.strip_ansi(row).lower()
                start = 0
                while True:
                    found = plain.find(needle, start)
                    if found < 0:
                        break
                    self.search_matches.append((row_index, found, found + len(needle)))
                    start = found + max(1, len(needle))
        self.search_index = min(self.search_index, max(0, len(self.search_matches) - 1))

    def set_search(self, query: str, width: int) -> None:
        self.search = query
        self.search_index = 0
        self.lines(width)  # a fresh query must see the current rows
        self._recompute_matches()

    def search_next(self, delta: int, width: int, height: int) -> tuple[int, int, int] | None:
        """Cycle matches (``n``/``N``) and bring the current one into view."""
        if not self.search_matches:
            return None
        self.search_index = (self.search_index + delta) % len(self.search_matches)
        match = self.search_matches[self.search_index]
        total = self.total(width)
        body = max(1, height - 1)
        tail_space = max(1, body - self._pinned_rows)
        end = min(total, match[0] + max(1, tail_space // 2) + 1)
        self.scroll = max(0, min(self._max_scroll(width, height), total - end))
        self._set_follow()
        return match

    def highlight(self, rows: list[str]) -> list[str]:
        """Reverse-video the matches currently on screen."""
        if not self.search_matches or len(self._window) != len(rows):
            return rows
        where = {full: vis for vis, full in enumerate(self._window) if full is not None}
        spans: dict[int, list[tuple[int, int]]] = {}
        for row_index, start, end in self.search_matches:
            visible_index = where.get(row_index)
            if visible_index is not None:
                spans.setdefault(visible_index, []).append((start, end))
        return [
            ansi.highlight_matches(row, spans[index]) if index in spans else row
            for index, row in enumerate(rows)
        ]

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
            window = list(rows)
            where: list[int | None] = list(range(len(rows)))
            window += [""] * (body - len(rows))
            where += [None] * (body - len(rows))
            self._window = [*where, None]
            return [*window, ""]

        pinned = min(self._pinned_rows, body - 1, len(rows))
        tail_space = max(1, body - pinned)
        self.scroll = max(0, min(self.scroll, self._max_scroll(width, height)))
        if self.scroll == 0 and not self.follow:
            self._set_follow()
        end = len(rows) - self.scroll
        start = max(pinned, end - tail_space)
        window = list(rows[:pinned]) + list(rows[start:end])
        window = window[:body]
        where = list(range(pinned)) + list(range(start, start + len(window) - pinned))
        where = where[:body]
        window += [""] * (body - len(window))
        where += [None] * (body - len(window))
        self._window = [*where, None]
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

    def _set_follow(self) -> None:
        if self.scroll <= 0:
            self.follow = True
            self.unseen = 0
        else:
            self.follow = False

    def bottom(self) -> None:
        """Return to the tail and clear the new-rows counter (``end``/submit)."""
        self.scroll = 0
        self._set_follow()

    def scroll_by(self, delta: int, width: int, height: int) -> None:
        self.scroll = max(0, min(self._max_scroll(width, height), self.scroll + delta))
        self._set_follow()

    def page(self, direction: int, width: int, height: int) -> None:
        self.scroll_by(direction * max(1, height - 2), width, height)
