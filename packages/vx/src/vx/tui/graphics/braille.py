"""Braille rendering: eight dots (2x4) per character cell.

Braille subpixels are physically square in a 1:2 cell, unlike quadrant
subpixels, so the source grid maps directly onto 2 columns x 4 rows per cell.
That is four times the pixel rows of half blocks and twice the rows of
quadrants — the sharpest text-only option, at the cost of small, separated
dots.

Dots do not tile: the cell background shows between them, so transparent
subpixels are composited over it and the fit prefers a background that covers
at least half the cell. Whole rows/columns use the solid half-block glyphs
instead of dots, and a uniform cell uses ``█``, which keeps flat areas solid.
"""

from __future__ import annotations

from vx.tui import ansi
from vx.tui.graphics.blocks import ALPHA_MIN, MODE_256, MODE_TRUE, _bg, _fg
from vx.tui.graphics.png import RGBA, scale

#: Matches the dark terminal theme; callers can pass ``bg`` to ``rows``.
BACKGROUND = (24, 24, 30)
SEAM_BIAS = 4000

# Dot bits by (row, column): braille dot numbers 1,2,3,7 on the left and
# 4,5,6,8 on the right.
_DOTS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
ALL = 0xFF
_LEFT, _RIGHT = 0x47, 0xB8
_TOP, _BOTTOM = 0x1B, 0xE4
_SOLID = {
    ALL: "\u2588",  # █
    _LEFT: "\u258c",  # ▌
    _RIGHT: "\u2590",  # ▐
    _TOP: "\u2580",  # ▀
    _BOTTOM: "\u2584",  # ▄
}


def box(width: int, height: int, max_cols: int, max_rows: int) -> tuple[int, int]:
    """Pixel box for a cell area, with square subpixels."""
    max_cols, max_rows = max(1, max_cols), max(1, max_rows)
    px_w = max(2, 2 * max_cols)
    px_h = max(4, 4 * round(px_w * height / (4 * width)))
    if px_h > max_rows * 4:
        px_h = max(4, max_rows * 4)
        px_w = max(2, 2 * round(px_h * width / (2 * height)))
    return px_w, px_h


def fit(width: int, height: int, max_cols: int, max_rows: int) -> tuple[int, int]:
    """Cell box the braille layout fills."""
    px_w, px_h = box(width, height, max_cols, max_rows)
    return px_w // 2, px_h // 4


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


def _candidates(
    colours: list[tuple[int, int, int]], bg: tuple[int, int, int]
) -> list[tuple[int, int, int]]:
    """Bucket eight subpixels into a handful of representative colours."""
    buckets: dict[tuple[int, int, int], tuple[int, list[int]]] = {}
    for colour in (*colours, bg):
        key = (colour[0] >> 4, colour[1] >> 4, colour[2] >> 4)
        slot = buckets.get(key)
        if slot is None:
            buckets[key] = (1, list(colour))
        else:
            count, acc = slot
            acc[0] += colour[0]
            acc[1] += colour[1]
            acc[2] += colour[2]
            buckets[key] = (count + 1, acc)
    ranked = sorted(buckets.values(), key=lambda slot: slot[0], reverse=True)[:6]
    result = []
    for count, acc in ranked:
        result.append((round(acc[0] / count), round(acc[1] / count), round(acc[2] / count)))
    if bg not in result:
        result.append(bg)
    return result


def _pair(
    pixels: list[tuple[int, int, int]], candidates: list[tuple[int, int, int]]
) -> tuple[tuple[int, int, int], tuple[int, int, int], int]:
    """Best fg/bg pair and dot mask for one cell's eight subpixels."""
    best_fg = best_bg = candidates[0]
    best_mask = 0
    best_error = -1
    for fg in candidates:
        for bg in candidates:
            if fg == bg:
                continue
            mask = 0
            error = 0
            for row, dots in enumerate(_DOTS):
                for col, bit in enumerate(dots):
                    colour = pixels[row * 2 + col]
                    df, db = _distance(colour, fg), _distance(colour, bg)
                    if df <= db:
                        mask |= bit
                        error += df
                    else:
                        error += db
            if mask.bit_count() > 4:
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
    for x in range(px_w // 2):
        colours: list[tuple[int, int, int]] = []
        visible = False
        for dy in range(4):
            base = ((cell_y * 4 + dy) * px_w + x * 2) * 4
            for dx in range(2):
                o = base + dx * 4
                if data[o + 3] >= alpha_min:
                    visible = True
                colours.append(_composite(data[o], data[o + 1], data[o + 2], data[o + 3], bg))
        if not visible:
            if last_bg is not None:
                parts.append("\x1b[49m")
                last_bg = None
            parts.append(" ")
            continue
        fg, cell_bg, mask = _pair(colours, _candidates(colours, bg))
        if mask == 0:
            # Every dot picked the background colour (typical of a uniform
            # cell): paint it solid rather than emitting an empty braille char.
            fg, glyph = cell_bg, _SOLID[ALL]
        else:
            glyph = _SOLID.get(mask) or chr(0x2800 + mask)
        if fg != last_fg:
            parts.append(_fg(fg, mode))
            last_fg = fg
        if glyph not in _SOLID.values() and mask and cell_bg != last_bg:
            parts.append("\x1b[49m" if cell_bg == bg else _bg(cell_bg, mode))
            last_bg = cell_bg
        parts.append(glyph)
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
    for cell_y in range(px_h // 4):
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
    """Fit ``pixels`` into a cell box and return braille rows."""
    width, height, _ = pixels
    px_w, px_h = box(width, height, max_cols, max_rows)
    return rows(pixels, px_w, px_h, alpha_min=alpha_min, mode=mode, bg=bg)


__all__ = ["ALPHA_MIN", "BACKGROUND", "MODE_256", "MODE_TRUE", "box", "fit", "render", "rows"]
