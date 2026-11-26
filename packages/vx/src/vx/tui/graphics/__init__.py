"""Terminal graphics for the vx TUI.

Two layers, one entry point:

* ``blocks`` — half-block cells, works in every terminal, needs no detection.
* ``quadrants`` — four pixels per cell, for fonts with quadrant glyphs.
* ``braille`` — eight dots (2x4) per cell, for fonts with braille glyphs.
* ``protocols`` — sixel / kitty / iTerm2 pixels, only where the terminal
  actually speaks them.

``detect`` answers which of those a session can use. It prefers probing the
live terminal (a DA1 reply tells us about sixel, ``CSI ? u`` about kitty, and
``CSI 16 t`` the size of a cell in pixels) and falls back to environment
variables when the session cannot be queried, e.g. inside tmux or over a pipe.

Set ``VTX_GFX=blocks|quadrants|braille|sixel|kitty|iterm|auto`` to override the result
— useful for testing a protocol the probe cannot confirm. In VS Code the DA1
reply advertises sixel only when ``terminal.integrated.enableImages`` is on;
``TERM=xterm-sixel`` is honoured as a fallback hint for that case.
"""

from __future__ import annotations

import os
import re
import select
import time
from dataclasses import dataclass, field

from vx.tui.graphics import blocks, braille, palette, png, protocols, quadrants
from vx.tui.graphics.png import RGBA

__all__ = [
    "RGBA",
    "Caps",
    "blocks",
    "braille",
    "detect",
    "palette",
    "png",
    "protocols",
    "quadrants",
    "render",
]

_DA1 = re.compile(rb"\x1b\[\?([0-9;]*)c")
_CELL = re.compile(rb"\x1b\[6;(\d+);(\d+)t")
_KITTY = re.compile(rb"\x1b\[\?(\d+)u")

#: One write covers three questions: cell pixel size, DA1, kitty capabilities.
_PROBE = b"\x1b[16t\x1b[c\x1b[?u"

#: Fallback cell size when the terminal will not say (8x16 is the classic VGA).
DEFAULT_CELL = (8, 16)

#: Backends ordered best first, used by ``Caps.best``.
_PREFERENCE = ("kitty", "sixel", "iterm")

_ENV_KITTY = ("xterm-kitty", "xterm-ghostty", "ghostty")
#: Programs whose sixel support is unconditional (VS Code needs a setting, so
#: it is probe-only: better to draw cells than splatter a DCS into the buffer).
_ENV_SIXEL_PROGRAMS = ("WezTerm", "iTerm.app")
#: TERM values that explicitly promise sixel. VS Code with images enabled
#: advertises `4` in DA1, but this is the escape hatch when a reply is lost.
_ENV_SIXEL_TERMS = ("xterm-sixel", "sixel")


@dataclass
class Caps:
    """What the current terminal can draw, plus the pixel size of a cell."""

    sixel: bool = False
    kitty: bool = False
    iterm: bool = False
    cell: tuple[int, int] = field(default_factory=lambda: DEFAULT_CELL)
    probed: bool = False
    source: str = "env"
    prefer: str = ""

    @property
    def backends(self) -> tuple[str, ...]:
        found = [
            name
            for name, ok in (("kitty", self.kitty), ("sixel", self.sixel), ("iterm", self.iterm))
            if ok
        ]
        return (*found, "blocks", "quadrants", "braille")

    @property
    def cycle_backends(self) -> tuple[str, ...]:
        """Every backend ``g`` can reach, including unprobed pixel protocols.

        Only the two cell backends are cycled by hand; pixel protocols stay
        opt-in via ``VTX_GFX`` / ``--gfx`` or automatic detection. ``best()``
        still uses ``backends`` and never picks an undetected protocol itself.
        """
        return ("blocks", "quadrants", "braille")

    def best(self) -> str:
        if self.prefer in self.backends:
            return self.prefer
        for name in _PREFERENCE:
            if name in self.backends:
                return name
        return "blocks"

    def pixel_box(self, cols: int, rows: int) -> tuple[int, int]:
        """Pixels covered by a ``cols`` x ``rows`` cell rectangle."""
        cell_w, cell_h = self.cell
        return max(1, cols * cell_w), max(1, rows * cell_h)


def _env_caps(env: dict[str, str]) -> Caps:
    term = env.get("TERM", "")
    program = env.get("TERM_PROGRAM", "")
    caps = Caps(source="env")
    if any(token in term for token in _ENV_KITTY) or env.get("KITTY_WINDOW_ID"):
        caps.kitty = True
    if any(token in term for token in _ENV_SIXEL_TERMS):
        caps.sixel = True
    if program == "WezTerm":
        caps.kitty = caps.iterm = caps.sixel = True
    elif program in _ENV_SIXEL_PROGRAMS:
        caps.sixel = caps.iterm = program == "iTerm.app"
    if env.get("TMUX"):
        # tmux forwards sixel but strips kitty and iTerm2 payloads unless
        # `allow-passthrough` is set, so do not promise them.
        caps.kitty = caps.iterm = False
    return caps


def _parse(reply: bytes, caps: Caps) -> Caps:
    found_cell = _CELL.search(reply)
    if found_cell:
        caps.cell = (max(1, int(found_cell.group(2))), max(1, int(found_cell.group(1))))
    da1 = _DA1.search(reply)
    if da1:
        caps.sixel = any(part == b"4" for part in da1.group(1).split(b";"))
    kitty = _KITTY.search(reply)
    if kitty:
        caps.kitty = bool(int(kitty.group(1)) & 1)
    return caps


def detect(term=None, *, timeout: float = 0.3, env: dict[str, str] | None = None) -> Caps:
    """Capabilities for this session; probes the terminal when one is given."""
    environ = dict(os.environ if env is None else env)
    caps = _env_caps(environ)
    forced = environ.get("VTX_GFX", "").lower()
    if forced and forced != "auto":
        caps.sixel = forced == "sixel"
        caps.kitty = forced == "kitty"
        caps.iterm = forced == "iterm"
        caps.source = "forced"
        caps.prefer = forced
        return caps
    fd = getattr(term, "_fd", None)
    if fd is None:
        return caps
    try:
        os.write(fd, _PROBE)
    except OSError:
        return caps
    deadline = time.monotonic() + timeout
    reply = b""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            ready, _, _ = select.select([fd], [], [], remaining)
        except (OSError, ValueError):
            break
        if not ready:
            break
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        reply += chunk
        if b"c" in reply and b"u" in reply:
            break
    if reply:
        caps.probed = True
        caps.source = "probe"
        _parse(reply, caps)
        if environ.get("TMUX"):
            # Under tmux a DA1 reply describes tmux, not the terminal looking at
            # it: tmux 3.4 answers `4` (sixel) for any client, and a wrong guess
            # dumps a DCS full of pixels into the buffer. Keep only what the
            # environment already vouched for and let `VTX_GFX` opt in.
            caps.sixel = caps.sixel and _env_caps(environ).sixel
    return caps


def render(
    pixels, max_cols: int, max_rows: int, *, backend: str = "blocks", mode: str = blocks.MODE_TRUE
) -> list[str]:
    """Styled rows for a pixel buffer, on the requested cell backend."""
    if backend == "quadrants":
        return quadrants.render(pixels, max_cols, max_rows, mode=mode)
    if backend == "braille":
        return braille.render(pixels, max_cols, max_rows, mode=mode)
    return blocks.render(pixels, max_cols, max_rows, mode=mode)


def load(path: str) -> RGBA:
    return png.load(path)
