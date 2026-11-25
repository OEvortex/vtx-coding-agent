"""The xterm-256 palette as colour maths.

``vx.tui.theme`` already speaks in ``38;5;n``, so graphics quantise to the same
256 entries: one renderer pipeline for every terminal, no truecolor assumption,
and tmux/screen stay happy. The 6x6x6 cube plus the 24-step grey ramp is the
same layout xterm, VS Code and every other terminal install.
"""

from __future__ import annotations

_CUBE = (0, 95, 135, 175, 215, 255)

_CACHE: dict[tuple[int, int, int], int] = {}


def _ramp() -> tuple[tuple[int, int, int], ...]:
    """The 256 entries in index order, so encoders can define them verbatim."""
    entries: list[tuple[int, int, int]] = []
    basics = (
        (0, 0, 0),
        (128, 0, 0),
        (0, 128, 0),
        (128, 128, 0),
        (0, 0, 128),
        (128, 0, 128),
        (0, 128, 128),
        (192, 192, 192),
        (128, 128, 128),
        (255, 0, 0),
        (0, 255, 0),
        (255, 255, 0),
        (0, 0, 255),
        (255, 0, 255),
        (0, 255, 255),
        (255, 255, 255),
    )
    entries.extend(basics)
    for r in _CUBE:
        for g in _CUBE:
            for b in _CUBE:
                entries.append((r, g, b))
    for step in range(24):
        level = 8 + step * 10
        entries.append((level, level, level))
    return tuple(entries)


PALETTE = _ramp()


def nearest(r: int, g: int, b: int) -> int:
    """The genuinely nearest entry, memoised per colour.

    A frame has at most a few hundred distinct colours after scaling, so 256
    comparisons each is nothing, and exact matching matters: a shortcut that
    snapped channels to the cube independently turned anti-aliased outline
    pixels (70, 33, 42) into bright maroon and left red fringing round every
    sprite.
    """
    key = (r, g, b)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    best = 16
    best_distance = 1 << 30
    # Skip the first 16 entries: those are the terminal's *theme* colours, so
    # a Solarized or Gruvbox profile would repaint them and shift the image.
    # Entries 16-255 are fixed by the xterm specification.
    for index in range(16, len(PALETTE)):
        pr, pg, pb = PALETTE[index]
        distance = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2
        if distance < best_distance:
            best_distance = distance
            best = index
            if not distance:
                break
    _CACHE[key] = best
    return best


def fg(index: int) -> str:
    return f"\x1b[38;5;{index}m"


def bg(index: int) -> str:
    return f"\x1b[48;5;{index}m"


def rgb(index: int) -> tuple[int, int, int]:
    return PALETTE[index]


def as_percent(index: int) -> tuple[int, int, int]:
    """0-100 channels for sixel's colour introducer."""
    r, g, b = PALETTE[index]
    return round(r * 100 / 255), round(g * 100 / 255), round(b * 100 / 255)
