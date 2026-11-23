"""Transcript viewport: block cache, spacing, scroll and the visible window.

fx keeps the welcome banner pinned while newer rows scroll beneath it; the
same split is applied here, so the brand line never leaves the screen.
"""

from __future__ import annotations

from vx.tui import blocks


class Viewport:
    def __init__(self) -> None:
        self.blocks: list[blocks.Block] = []
        self.scroll = 0
        self._width = 0
        self._rows: list[str] = []
        self._kinds: list[str | None] = []
        self._dirty = True
        self._pinned_rows = 2

    # -- content -----------------------------------------------------------

    def add(self, block: blocks.Block) -> None:
        self.blocks.append(block)
        self._dirty = True
        self.scroll = 0

    def replace_all(self, blocks: list[blocks.Block]) -> None:
        self.blocks = list(blocks)
        self._dirty = True
        self.scroll = 0

    def clear(self) -> None:
        self.blocks.clear()
        self._rows = []
        self._kinds = []
        self.scroll = 0
        self._dirty = False

    def touch(self) -> None:
        self._dirty = True

    def tail(self) -> blocks.Block | None:
        return self.blocks[-1] if self.blocks else None

    # -- measurement -------------------------------------------------------

    def _rebuild(self, width: int) -> None:
        rows: list[str] = []
        kinds: list[str | None] = []
        previous: str | None = None
        for block in self.blocks:
            block_rows = block.rows(width)
            if not block_rows:
                continue
            gap = blocks.spacing(previous, block.kind)
            for _ in range(gap):
                rows.append("")
                kinds.append(previous)
            for row in block_rows:
                rows.append(row)
                kinds.append(block.kind)
            previous = block.kind
        self._rows = rows
        self._kinds = kinds
        self._width = width
        self._dirty = False

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
        max_scroll = max(0, len(rows) - pinned - tail_space)
        self.scroll = max(0, min(self.scroll, max_scroll))
        end = len(rows) - self.scroll
        start = max(pinned, end - tail_space)
        window = list(rows[:pinned]) + list(rows[start:end])
        window = window[:body]
        window += [""] * (body - len(window))
        return [*window, ""]

    # -- scrolling ---------------------------------------------------------

    def scroll_by(self, delta: int, width: int, height: int) -> None:
        body = max(1, height - 2)
        max_scroll = max(0, len(self.lines(width)) - self._pinned_rows - body)
        self.scroll = max(0, min(max_scroll, self.scroll + delta))

    def page(self, direction: int, width: int, height: int) -> None:
        self.scroll_by(direction * max(1, height - 2), width, height)

    def at_bottom(self) -> bool:
        return self.scroll == 0


def blank_row() -> str:
    return ""
