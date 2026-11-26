"""Quadrant rendering: four pixels per character cell.

Half blocks (``blocks.py``) split a cell into 1x2 pixels, which is safe in every
font but wastes half of a cell's horizontal resolution. Quadrant block glyphs
split it into 2x2, doubling the horizontal pixel count without giving up
truecolor: a cell still carries one foreground and one background colour.

The catch is that a quadrant cell has no per-pixel transparency. A transparent
subpixel is therefore composited over the terminal background *before* the
fg/bg fit; leaving it out of the fit instead paints it with a sprite colour and
puts coloured specks around the edges. The 14 non-uniform fg/bg assignments
are searched exhaustively over the composited colours, and the one with the
lowest squared error wins.

U+2596-U+259F are present in most modern monospace fonts, but a few render
them with seams. Use ``blocks`` when a font cannot be trusted, e.g. a plain
``g`` press cycles to this backend only when the user asks for it.
"""

from __future__ import annotations

from vx.tui import ansi
from vx.tui.graphics.blocks import ALPHA_MIN, MODE_256, MODE_TRUE, _bg, _fg
from vx.tui.graphics.png import RGBA, scale

#: Background the transparent subpixels are composited onto. This matches the
#: dark terminal theme and the snapshot harness; callers with a different
#: theme background can pass ``bg`` to ``rows``.
BACKGROUND = (24, 24, 30)

# Bit per quadrant: top-left, top-right, bottom-left, bottom-right.
_TL, _TR, _BL, _BR = 8, 4, 2, 1
_BITS = (_TL, _TR, _BL, _BR)

#: Unicode block elements for every non-empty 2x2 pattern.
GLYPHS = {
    _TL | _TR: "\u2580",  # ▀
    _BL | _BR: "\u2584",  # ▄
    _TL | _BL: "\u258c",  # ▌
    _TR | _BR: "\u2590",  # ▐
    _TL: "\u2598",  # ▘
    _TR: "\u259d",  # ▝
    _BL: "\u2596",  # ▖
    _BR: "\u2597",  # ▗
    _TL | _BR: "\u259a",  # ▚
    _TR | _BL: "\u259e",  # ▞
    _TL | _TR | _BL: "\u259b",  # ▛
    _TL | _TR | _BR: "\u259c",  # ▜
    _TL | _BL | _BR: "\u2599",  # ▙
    _TR | _BL | _BR: "\u259f",  # ▟
    _TL | _TR | _BL | _BR: "\u2588",  # █
}


def box(width: int, height: int, max_cols: int, max_rows: int) -> tuple[int, int]:
    """Pixel box for a cell area, with square source pixels.

    A cell covers 2x2 subpixels and is physically about twice as tall as it is
    wide, so a square source pixel maps to twice as many columns as rows: the
    effective grid is 2*max_cols x 2*max_rows, aspect-corrected.
    """
    max_cols, max_rows = max(1, max_cols), max(1, max_rows)
    px_w = max(2, 2 * max_cols)
    px_h = max(2, 2 * round(px_w * height / (4 * width)))
    if px_h > max_rows * 2:
        px_h = max(2, max_rows * 2)
        px_w = max(2, 2 * round(px_h * width / height))
    return px_w, px_h


def fit(width: int, height: int, max_cols: int, max_rows: int) -> tuple[int, int]:
    """Cell box the quadrant layout fills."""
    px_w, px_h = box(width, height, max_cols, max_rows)
    return px_w // 2, px_h // 2


def _composite(r: int, g: int, b: int, a: int, bg: tuple[int, int, int]) -> tuple[int, int, int]:
    if a >= 255:
        return r, g, b
    t = a / 255
    return (
        round(r * t + bg[0] * (1 - t)),
        round(g * t + bg[1] * (1 - t)),
        round(b * t + bg[2] * (1 - t)),
    )


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


#: Most fonts leave a hairline gap between quadrant glyphs, through which the
#: cell background shows. Biasing the fit towards a background that covers at
#: least half the cell makes those hairlines blend into the sprite instead of
#: drawing a visible grid across it (measured ~24% less seam contrast).
SEAM_BIAS = 4000


def _pair(
    pixels: list[tuple[int, int, int]], candidates: list[tuple[int, int, int]]
) -> tuple[tuple[int, int, int], tuple[int, int, int], int]:
    """Best fg/bg pair and quadrant mask for one cell's four subpixels."""
    best_fg = best_bg = candidates[0]
    best_mask = 0
    best_error = -1
    for fg in candidates:
        for bg in candidates:
            if fg == bg:
                continue
            mask = 0
            error = 0
            for bit, colour in zip(_BITS, pixels, strict=True):
                df, db = _distance(colour, fg), _distance(colour, bg)
                if df <= db:
                    mask |= bit
                    error += df
                else:
                    error += db
            if mask.bit_count() > 2:
                error += SEAM_BIAS
            if best_error < 0 or error < best_error:
                best_error, best_fg, best_bg, best_mask = error, fg, bg, mask
    return best_fg, best_bg, best_mask


def _row(
    data: bytes, px_w: int, cell_y: int, alpha_min: int, mode: str, bg: tuple[int, int, int]
) -> str:
    parts: list[str] = []
    last_fg: tuple[int, int, int] | None = None
    last_bg: tuple[int, int, int] | None = None
    styled = False
    top = cell_y * 2 * px_w * 4
    bottom = top + px_w * 4
    for x in range(px_w // 2):
        colours: list[tuple[int, int, int]] = []
        visible = False
        for row in (top, bottom):
            for half in range(2):
                o = row + (x * 2 + half) * 4
                if data[o + 3] >= alpha_min:
                    visible = True
                colours.append(_composite(data[o], data[o + 1], data[o + 2], data[o + 3], bg))
        if not visible:
            # No pixel here: let the terminal's own background show through.
            if last_bg is not None:
                parts.append("\x1b[49m")
                last_bg = None
            parts.append(" ")
            continue
        candidates = list(dict.fromkeys([*colours, bg]))
        fg, cell_bg, mask = _pair(colours, candidates)
        if mask == 0:
            # Every quadrant picked the background colour; a full block with
            # that colour as fg avoids painting an unwanted colour behind it.
            fg, mask = cell_bg, 0b1111
        if fg != last_fg:
            parts.append(_fg(fg, mode))
            last_fg = fg
        if mask == 0b1111:
            parts.append(GLYPHS[mask])
        else:
            if cell_bg != last_bg:
                # The default background is what an empty cell shows too, so
                # SGR 49 keeps the sprite edge indistinguishable from the
                # theme instead of hard-coding the assumed RGB value.
                parts.append("\x1b[49m" if cell_bg == bg else _bg(cell_bg, mode))
                last_bg = cell_bg
            parts.append(GLYPHS[mask])
        styled = True
    if not styled:
        return ""
    parts.append(ansi.RESET)
    return "".join(parts)


def rows(
    pixels: RGBA,
    px_w: int,
    px_h: int,
    *,
    alpha_min: int = ALPHA_MIN,
    mode: str = MODE_TRUE,
    bg: tuple[int, int, int] = BACKGROUND,
) -> list[str]:
    """Pixel buffer -> one styled string per cell row."""
    _w, _h, data = scale(pixels, px_w, px_h)
    if not any(a >= alpha_min for a in data[3::4]):
        return []
    out: list[str] = []
    for cell_y in range(px_h // 2):
        out.append(_row(data, px_w, cell_y, alpha_min, mode, bg))
    while out and not out[-1]:
        out.pop()
    return out


def render(
    pixels: RGBA,
    max_cols: int,
    max_rows: int,
    *,
    alpha_min: int = ALPHA_MIN,
    mode: str = MODE_TRUE,
    bg: tuple[int, int, int] = BACKGROUND,
) -> list[str]:
    """Fit ``pixels`` into a cell box and return its quadrant rows."""
    width, height, _ = pixels
    px_w, px_h = box(width, height, max_cols, max_rows)
    return rows(pixels, px_w, px_h, alpha_min=alpha_min, mode=mode, bg=bg)


__all__ = [
    "ALPHA_MIN",
    "BACKGROUND",
    "GLYPHS",
    "MODE_256",
    "MODE_TRUE",
    "box",
    "fit",
    "render",
    "rows",
]
