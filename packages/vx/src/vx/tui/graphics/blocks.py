"""Half-block rendering: two pixels per character cell.

This is the only way to draw a picture in *every* terminal that can run the
rest of vx — VS Code, GNOME Terminal, tmux, Windows Terminal, Apple Terminal —
because it is nothing more exotic than styled text. U+2580 (upper half block)
carries two pixels: the foreground colour paints the top pixel, the background
the bottom one, which is why the terminal never needs to understand graphics.

One cell is treated as 1 pixel wide and 2 pixels tall, so a cell-accurate
aspect correction costs nothing: square pixels fall out of the geometry.

Two colour modes share that geometry. ``256`` uses the xterm palette the rest
of the theme speaks, which every terminal and every tmux config survives.
``true`` sends ``38;2`` triples, which is what pixel art actually needs: the
256-entry cube has no entry near an anti-aliased edge, so outlines picked up
red and magenta specks. Any terminal reporting ``COLORTERM=truecolor`` gets
``true`` by default.
"""

from __future__ import annotations

import os

from vx.tui import ansi
from vx.tui.graphics import palette
from vx.tui.graphics.png import RGBA, scale

#: Alpha below this reads as "no pixel here". Pixel art is mostly binary
#: alpha, but a downscaled sheet produces soft edges, and painting a 20%-opaque
#: shadow at full strength is exactly what made the outline look chewed up.
ALPHA_MIN = 110

UPPER = "\u2580"  # ▀
LOWER = "\u2584"  # ▄

MODE_256 = "256"
MODE_TRUE = "true"


def supports_truecolor(env: dict[str, str] | None = None) -> bool:
    """True when the terminal accepts 24-bit SGR colours."""
    environ = os.environ if env is None else env
    if environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return True
    return "direct" in environ.get("TERM", "").lower()


def box(width: int, height: int, max_cols: int, max_rows: int) -> tuple[int, int]:
    """Pixel box for a ``max_cols`` x ``max_rows`` cell area, aspect preserved."""
    max_cols = max(1, max_cols)
    max_rows = max(1, max_rows)
    px_w = max(1, max_cols)
    px_h = max(2, round(px_w * height / width))
    if px_h > max_rows * 2:
        px_h = max(2, max_rows * 2)
        px_w = max(1, round(px_h * width / height))
    return px_w, px_h


def rows(
    pixels: RGBA, px_w: int, px_h: int, *, alpha_min: int = ALPHA_MIN, mode: str = MODE_TRUE
) -> list[str]:
    """Pixel buffer -> one styled string per cell row.

    The resize uses alpha-weighted averaging, which keeps both flat sticker
    art and detailed anime sprites readable: a majority vote preserves hard
    edges in flat art but throws away the shading and fine line work that
    detailed sheets are made of.

    Fully transparent rows come back as ``""`` so callers can collapse them
    instead of reserving blank space for a sprite's empty margins.
    """
    _w, _h, data = scale(pixels, px_w, px_h)
    alpha = data[3::4]
    if not any(a >= alpha_min for a in alpha):
        return []
    out: list[str] = []
    for cell_y in range(px_h // 2):
        out.append(_row(data, px_w, cell_y, alpha_min, mode))
    while out and not out[-1]:
        out.pop()
    return out


def fit(width: int, height: int, max_cols: int, max_rows: int) -> tuple[int, int]:
    """Cell box the half-block layout actually fills."""
    px_w, px_h = box(width, height, max_cols, max_rows)
    return px_w, px_h // 2


def _fg(rgb: tuple[int, int, int], mode: str) -> str:
    if mode == MODE_TRUE:
        return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"
    return palette.fg(palette.nearest(*rgb))


def _bg(rgb: tuple[int, int, int], mode: str) -> str:
    if mode == MODE_TRUE:
        return f"\x1b[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m"
    return palette.bg(palette.nearest(*rgb))


def _row(data: bytes, px_w: int, cell_y: int, alpha_min: int, mode: str) -> str:
    parts: list[str] = []
    last_fg: tuple[int, int, int] | None = None
    last_bg: tuple[int, int, int] | None = None
    styled = False
    top_row = cell_y * 2 * px_w * 4
    bottom_row = top_row + px_w * 4
    for x in range(px_w):
        top = top_row + x * 4
        bottom = bottom_row + x * 4
        top_rgb = (data[top], data[top + 1], data[top + 2])
        bottom_rgb = (data[bottom], data[bottom + 1], data[bottom + 2])
        top_on = data[top + 3] >= alpha_min
        bottom_on = data[bottom + 3] >= alpha_min
        if not top_on and not bottom_on:
            # A space still paints the *current* background. Without a reset it
            # inherits the previous sprite pixel's colour and fills the empty
            # margin with solid stripes instead of showing the theme behind it.
            if last_bg is not None:
                parts.append("\x1b[49m")
                last_bg = None
            parts.append(" ")
            continue
        if top_on and bottom_on:
            if top_rgb != last_fg:
                parts.append(_fg(top_rgb, mode))
                last_fg = top_rgb
            if bottom_rgb != last_bg:
                parts.append(_bg(bottom_rgb, mode))
                last_bg = bottom_rgb
            parts.append(UPPER)
            styled = True
            continue
        # Only one half is present: keep the other half the terminal's own
        # background (SGR 49) so the sprite edge blends into the theme.
        colour = top_rgb if top_on else bottom_rgb
        if colour != last_fg:
            parts.append(_fg(colour, mode))
            last_fg = colour
        if last_bg is not None:
            parts.append("\x1b[49m")
            last_bg = None
        parts.append(UPPER if top_on else LOWER)
        styled = True
    if not styled:
        return ""
    parts.append(ansi.RESET)
    return "".join(parts)


def render(
    pixels: RGBA,
    max_cols: int,
    max_rows: int,
    *,
    alpha_min: int = ALPHA_MIN,
    mode: str = MODE_TRUE,
) -> list[str]:
    """Fit ``pixels`` into a cell box and return its rows."""
    width, height, _ = pixels
    px_w, px_h = box(width, height, max_cols, max_rows)
    return rows(pixels, px_w, px_h, alpha_min=alpha_min, mode=mode)
