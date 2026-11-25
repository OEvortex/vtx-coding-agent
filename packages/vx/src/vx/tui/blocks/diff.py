"""Unified and split diff transcript block."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vx.tui import ansi
from vx.tui import theme as _theme
from vx.tui.blocks.base import Block
from vx.tui.blocks.tools import _display_path


@dataclass
class DiffOutput(Block):
    """A patch's diff: a ruler gutter, word-wrapped, split when there is room.

    Narrow terminals get one column with the sign glued to the ruler
    (``206 │-``). Past :data:`DIFF_SPLIT_MIN_WIDTH` the same lines are paired
    into an old/new split, so the change reads left-to-right instead of top-to-
    bottom. Long lines wrap instead of being cut: a diff is the one place where
    the end of a line matters.
    """

    lines: list[tuple[str, str, str]] = field(default_factory=list)
    """List of (op, line_number, text) where op is '+', '-', or ' '."""
    path: str = ""
    """The file the lines belong to; empty when the block holds one file's diff."""
    #: The patch is still being written: the last row is left open with a caret.
    streaming: bool = False
    #: Its own kind, so spacing can keep it welded under the row that edited.
    kind: str = "diff"

    def rows(self, width: int) -> list[str]:
        if not self.lines:
            return []
        lines = self._with_caret() if self.streaming else self.lines
        t = _theme.current()
        out: list[str] = []
        if self.path:
            path = f"  {t.dim_style}± {_display_path(self.path)}{ansi.RESET}"
            out.append(ansi.clip_ellipsis(path, width))
        # A diff with nothing removed (a new file) would leave half the split
        # empty, so it keeps the full width and the one-column ruler.
        split = width >= DIFF_SPLIT_MIN_WIDTH and any(op == "-" for op, _ln, _x in lines)
        if split:
            out.extend(self._split(lines, width, t))
        else:
            out.extend(self._unified(lines, width, t))
        return out

    def _with_caret(self) -> list[tuple[str, str, str]]:
        """The rows so far, with a caret on the line the stream is still writing.

        The caret is the one mark that separates text that has arrived from text
        that is still coming. A line the stream has only reached the start of
        gets none: a bare caret under a ``+`` reads as an empty hunk line.
        """
        if not self.lines or not self.lines[-1][2]:
            return self.lines
        op, ln, text = self.lines[-1]
        return [*self.lines[:-1], (op, ln, text + STREAMING_CARET)]

    def _unified(self, lines: list[tuple[str, str, str]], width: int, t: Any) -> list[str]:
        """One column: ``  206 │- text``, wrapped under the code's own indent."""
        gw = _number_width(lines)
        # Two spaces, the number, " │" and the sign+space: the gutter is six
        # cells wider than the number, and a row one cell past `width` would
        # wrap the terminal and shift every row below it.
        cw = max(width - (gw + 6), 8)
        out: list[str] = []
        for op, ln, text in lines:
            if op == "~":
                out.append(_unchanged_row(ln, t))
                continue
            mark, style, bg = _diff_look(op, t)
            for index, chunk in enumerate(_code_chunks(_escape_text(text), cw)):
                if index == 0:
                    gutter = f"{t.dim_style}  {ln.rjust(gw)} │{ansi.RESET}{mark} "
                else:
                    gutter = f"{t.dim_style}  {' ' * gw} │{ansi.RESET}  "
                out.append(f"{gutter}{bg}{style}{chunk}{ansi.RESET}")
        return out

    def _split(self, lines: list[tuple[str, str, str]], width: int, t: Any) -> list[str]:
        """Two columns: the old file left, the new file right, aligned per change."""
        pairs = _split_pairs(lines)
        left_gw = _number_width([p for p in (left for left, _r in pairs if left) if p[0] != "~"])
        right_gw = _number_width(
            [p for p in (right for _l, right in pairs if right) if p[0] != "~"]
        )
        left_w = (width - len(_SPLIT_SEP)) // 2
        right_w = width - len(_SPLIT_SEP) - left_w
        left_cw = max(left_w - left_gw - 4, 8)
        right_cw = max(right_w - right_gw - 4, 8)
        dim = f"{t.dim_style}{_SPLIT_SEP}{ansi.RESET}"
        out: list[str] = []
        for left, right in pairs:
            # A run of unchanged lines spans both columns, so it breaks the
            # pairing instead of appearing twice.
            marker = left if left is not None else right
            if marker is not None and marker[0] == "~":
                out.append(_unchanged_row(marker[1], t))
                continue
            left_rows = _split_half(left, left_gw, left_cw, t)
            right_rows = _split_half(right, right_gw, right_cw, t)
            for index in range(max(len(left_rows), len(right_rows))):
                lrow = left_rows[index] if index < len(left_rows) else ""
                rrow = right_rows[index] if index < len(right_rows) else ""
                out.append(f"{_pad(lrow, left_w)}{dim}{rrow}")
        return out


#: Width at which a diff is paired into an old/new split instead of one column.
DIFF_SPLIT_MIN_WIDTH = 110

#: The cursor on a half-written row of a diff whose patch is still arriving.
STREAMING_CARET = "\u258f"
_SPLIT_SEP = " │ "


def _number_width(lines: list[tuple[str, str, str]]) -> int:
    return max((len(ln) for _op, ln, _text in lines if ln), default=1)


def _diff_look(op: str, t: Any) -> tuple[str, str, str]:
    """The sign, text style and background for one diff line."""
    if op == "+":
        return f"{t.diff_added_marker_style}+{ansi.RESET}", t.diff_added_style, "\x1b[48;5;22m"
    if op == "-":
        return f"{t.diff_removed_marker_style}-{ansi.RESET}", t.diff_removed_style, "\x1b[48;5;52m"
    return " ", t.diff_ctx_style, ""


def _unchanged_row(count: str, t: Any) -> str:
    """The elision row for a run of unchanged lines above, between or below."""
    lines = int(count) if count.isdigit() else 0
    noun = "line" if lines == 1 else "lines"
    return f"  {t.dim_style}⋯ {lines} {noun} unchanged{ansi.RESET}"


def _split_half(line: tuple[str, str, str] | None, gw: int, cw: int, t: Any) -> list[str]:
    """One side of a split row, wrapped to the column's text width."""
    if line is None:
        return []
    op, ln, text = line
    mark, style, bg = _diff_look(op, t)
    out: list[str] = []
    for index, chunk in enumerate(_code_chunks(_escape_text(text), cw)):
        if index == 0:
            gutter = f"{t.dim_style}{ln.rjust(gw)} │{ansi.RESET}{mark} "
        else:
            gutter = f"{t.dim_style}{' ' * gw} │{ansi.RESET}  "
        out.append(f"{gutter}{bg}{style}{chunk}{ansi.RESET}")
    return out


def _code_chunks(text: str, cw: int) -> list[str]:
    """Wrap code at ``cw``, hanging continuation rows under its own indent.

    Every row it returns fits in ``cw`` cells: an indent that ate the whole
    column, or a floor on the wrap width, would push the row past the terminal
    and wrap the frame itself.
    """
    body = text.lstrip(" ")
    indent = min(len(text) - len(body), max(cw // 2, 0), max(cw - 1, 0))
    if not body:
        return [""]
    budget = max(cw - indent, 1)
    chunks: list[str] = []
    for chunk in ansi.wrap(body, budget):
        # wrap() never splits a word that starts the row, so a long identifier
        # can still come back wider than the column; a diff row may not be.
        chunks.extend(ansi.hard_wrap(chunk, budget) if ansi.width(chunk) > budget else [chunk])
    hang = " " * indent
    return [hang + chunks[0], *(hang + chunk for chunk in chunks[1:])]


def _pad(text: str, col_w: int) -> str:
    """Pad styled text out to ``col_w`` cells so the divider stays straight."""
    return text + " " * max(col_w - ansi.width(text), 0)


def _split_pairs(
    lines: list[tuple[str, str, str]],
) -> list[tuple[tuple[str, str, str] | None, tuple[str, str, str] | None]]:
    """Pair removed lines with added ones so the split columns line up."""
    pairs: list[tuple[tuple[str, str, str] | None, tuple[str, str, str] | None]] = []
    index = 0
    total = len(lines)
    while index < total:
        op, _ln, _text = lines[index]
        if op == "~":
            # A run of unchanged lines belongs to neither column.
            pairs.append((lines[index], None))
            index += 1
            continue
        if op == " ":
            pairs.append((lines[index], lines[index]))
            index += 1
            continue
        removed: list[tuple[str, str, str]] = []
        added: list[tuple[str, str, str]] = []
        while index < total and lines[index][0] == "-":
            removed.append(lines[index])
            index += 1
        while index < total and lines[index][0] == "+":
            added.append(lines[index])
            index += 1
        for slot in range(max(len(removed), len(added))):
            left = removed[slot] if slot < len(removed) else None
            right = added[slot] if slot < len(added) else None
            pairs.append((left, right))
        if not removed and not added:
            # A lone '+' or '-' run not followed by its pair: keep the line.
            pairs.append((lines[index], None))
            index += 1
    return pairs


def _escape_text(text: str) -> str:
    """Strip trailing whitespace and control chars from diff text."""
    return text.rstrip()
