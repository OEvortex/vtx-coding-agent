"""Real pixel protocols: sixel, kitty graphics, iTerm2 inline images.

These are the high-fidelity path — they draw actual pixels, not characters —
and each one is a plain escape sequence, so no library is needed to emit them.
Support is fragmentary, which is why ``vx.tui.graphics.blocks`` stays the
default and this module is strictly opt-in:

* ``sixel``   — xterm, foot, WezTerm, iTerm2, Konsole, Windows Terminal 1.22+,
                and VS Code once ``terminal.integrated.enableImages`` is on.
* ``kitty``   — kitty, Ghostty, WezTerm, Konsole (partially).
* ``iterm``   — iTerm2, WezTerm, VS Code (same setting as sixel).
* GNOME Terminal, Warp, Apple Terminal: none of the above, ever.

Emitting is one half of the job. The other half is that these protocols paint
*pixels on top of cells*, which the row-diff canvas cannot erase or move, so a
caller has to redraw the image whenever the area under it is repainted.
"""

from __future__ import annotations

import base64

from vx.tui.graphics import palette
from vx.tui.graphics.png import RGBA, encode

_ALPHA_MIN = 32
_ST = "\x1b\\"


# -- sixel -------------------------------------------------------------------


def _rle(masks: list[str]) -> str:
    """Sixel run-length: ``$``-free repeats as ``!<count><char>``."""
    out: list[str] = []
    i = 0
    n = len(masks)
    while i < n:
        char = masks[i]
        j = i + 1
        while j < n and masks[j] == char:
            j += 1
        run = j - i
        if run > 3:
            out.append(f"!{run}{char}")
        else:
            out.append(char * run)
        i = j
    return "".join(out)


def sixel(pixels: RGBA, *, transparent: bool = True, _limit: int = 256) -> str:
    """Sixel DCS for a pixel buffer, quantised onto the xterm-256 palette.

    Reusing the palette as the sixel colour registers means the pixels look the
    same as the half-block fallback, and no median-cut pass is needed.
    """
    width, height, data = pixels
    codes = [-1] * (width * height)
    used: list[int] = []
    seen = [False] * _limit
    for i in range(width * height):
        o = i * 4
        if data[o + 3] < _ALPHA_MIN:
            continue
        index = palette.nearest(data[o], data[o + 1], data[o + 2])
        codes[i] = index
        if not seen[index]:
            seen[index] = True
            used.append(index)
    if not used:
        return ""

    out: list[str] = ["\x1bP0;1;0q" if transparent else "\x1bP0;0;0q"]
    out.append(f'"1;1;{width};{height}')
    for index in used:
        r, g, b = palette.as_percent(index)
        out.append(f"#{index};2;{r};{g};{b}")

    for band in range(0, height, 6):
        painted = False
        for index in used:
            masks: list[str] = []
            touched = False
            for x in range(width):
                bits = 0
                for k in range(6):
                    y = band + k
                    if y < height and codes[y * width + x] == index:
                        bits |= 1 << k
                if bits:
                    touched = True
                masks.append(chr(0x3F + bits))
            if not touched:
                continue
            out.append(f"#{index}{_rle(masks)}$")
            painted = True
        if painted:
            out.append("-")
    out.append(_ST)
    return "".join(out)


# -- kitty graphics protocol -------------------------------------------------


def _kitty_send(payload: str, *, control: str, chunk: int = 3072) -> str:
    """One kitty transmission; the payload is split into ``chunk``-sized pieces.

    The protocol caps a whole escape sequence at 4096 bytes, so the base64 goes
    out in chunks with ``m=1`` on every piece but the last.
    """
    parts: list[str] = []
    pieces = [payload[i : i + chunk] for i in range(0, len(payload), chunk)] or [""]
    for i, piece in enumerate(pieces):
        more = 1 if i < len(pieces) - 1 else 0
        sep = (
            f"\x1b_G{control},m={more};{piece}{_ST}" if piece else f"\x1b_G{control},m={more}{_ST}"
        )
        parts.append(sep)
    return "".join(parts)


def kitty(pixels: RGBA, cols: int, rows: int, *, image_id: int = 1) -> str:
    """Transmit a PNG and display it scaled into a ``cols`` x ``rows`` cell box."""
    width, height, data = pixels
    png = base64.b64encode(encode(width, height, data)).decode()
    return _kitty_send(png, control=f"a=T,f=100,c={cols},r={rows},i={image_id},q=2")


def kitty_delete(image_id: int = 1) -> str:
    return f"\x1b_Ga=d,d=i,i={image_id},q=2{_ST}"


# -- iTerm2 inline images ----------------------------------------------------


def iterm(pixels: RGBA, cols: int, rows: int, *, inline: int = 1) -> str:
    """OSC 1337 wrapper around a PNG, sized in cells."""
    width, height, data = pixels
    b64 = base64.b64encode(encode(width, height, data)).decode()
    return (
        f"\x1b]1337;File=inline={inline};size={len(b64)};"
        f"width={cols};height={rows};preserveAspectRatio=0:{b64}\x07"
    )


# -- dispatch ----------------------------------------------------------------


def paint(kind: str, pixels: RGBA, cols: int, rows: int, *, image_id: int = 1) -> str:
    """The escape sequence for ``kind``; empty for anything unsupported."""
    if kind == "sixel":
        return sixel(pixels)
    if kind == "kitty":
        return kitty(pixels, cols, rows, image_id=image_id)
    if kind == "iterm":
        return iterm(pixels, cols, rows)
    return ""
