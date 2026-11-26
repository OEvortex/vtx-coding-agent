"""Desktop pets from the chiikawa-dots-pets sprite sheets.

The sheets follow the "Codex pet v2" layout: 8 columns x 11 rows of 192x208
cells, one row per motion, transparent PNG. Frame timings below are the delays
recorded in the published GIFs for hachiware, converted to milliseconds.

Nothing here knows about terminals: a ``Pet`` hands out a cropped, scaled RGBA
buffer and the graphics layer decides how to draw it.
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from vx.tui import graphics
from vx.tui.graphics import blocks, png
from vx.tui.graphics.gif import Gif
from vx.tui.graphics.png import RGBA


class MotionView(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def label(self) -> str: ...


class Player(Protocol):
    """What the demo needs from a spritesheet pet or a GIF animation."""

    @property
    def motion(self) -> MotionView: ...

    def play(self, name: str) -> None: ...

    def advance(self, now: float | None = None) -> bool: ...

    def pixels(self) -> RGBA: ...

    def scaled(self, px_w: int, px_h: int) -> RGBA: ...

    def rows(
        self,
        max_cols: int,
        max_rows: int,
        *,
        mode: str = blocks.MODE_TRUE,
        backend: str = "blocks",
    ) -> list[str]: ...

    def fit(self, max_cols: int, max_rows: int) -> tuple[int, int]: ...


CELL_W, CELL_H = 192, 208
SHEET_COLS = 8
#: Rows 9 and 10 hold the 16 gaze directions, so `look` spans two sheet rows.
LOOK_ROW = 9
SHEET_ROWS = 11


@dataclass(frozen=True)
class Motion:
    """One animation row of the sheet, with its recorded frame delays in ms."""

    name: str
    row: int
    delays: tuple[int, ...]
    label: str = ""

    @property
    def frames(self) -> int:
        return len(self.delays)

    def cell(self, index: int) -> tuple[int, int]:
        """``(col, row)`` of frame ``index`` inside the sheet."""
        if self.row == LOOK_ROW:
            return index % SHEET_COLS, LOOK_ROW + index // SHEET_COLS
        return index, self.row


MOTIONS: dict[str, Motion] = {
    m.name: m
    for m in (
        Motion("idle", 0, (280, 110, 110, 140, 140, 320), "idle"),
        Motion("running-right", 1, (110,) * 8, "run →"),
        Motion("running-left", 2, (110,) * 8, "run ←"),
        Motion("waving", 3, (140, 140, 140, 280), "waving"),
        Motion("jumping", 4, (140, 140, 140, 140, 280), "jumping"),
        Motion("failed", 5, (280, 140, 140, 140, 520), "failed"),
        Motion("waiting", 6, (150, 150, 150, 150, 150, 260), "waiting"),
        Motion("running", 7, (120, 120, 120, 120, 120, 220), "working"),
        Motion("review", 8, (150, 150, 150, 150, 150, 280), "review"),
        Motion("look", LOOK_ROW, (350,) * 16, "look"),
    )
}

#: Cycle order for a demo or a status-driven owner.
ORDER = tuple(MOTIONS)


def find_sheet(pet: str = "hachiware") -> Path | None:
    """Where a pet's ``spritesheet.png`` lives, if it is installed at all."""
    direct = os.environ.get("VTX_PET_SHEET")
    if direct:
        path = Path(direct).expanduser()
        return path if path.is_file() else None
    roots = []
    pets_dir = os.environ.get("VTX_PETS_DIR")
    if pets_dir:
        roots.append(Path(pets_dir).expanduser())
    roots.append(Path.home() / ".codex" / "pets")
    roots.append(Path.cwd() / "pets")
    roots.append(Path(__file__).resolve().parents[2] / "pets")
    for root in roots:
        path = root / pet / "spritesheet.png"
        if path.is_file():
            return path
    return None


class Spritesheet:
    """A decoded sheet plus a crop cache, so frames are cheap to re-read."""

    def __init__(
        self, source: str | Path | RGBA, cell_w: int | None = None, cell_h: int | None = None
    ) -> None:
        # A decoded buffer is accepted too, for WebP/JPEG sheets that arrive
        # through an external decoder (the TUI itself stays stdlib-only).
        if isinstance(source, tuple):
            self.path = Path("")
            self._image = source
        else:
            self.path = Path(source)
            self._image = png.load(str(self.path))
        self.width, self.height, _ = self._image
        self.cell_w = cell_w or CELL_W
        self.cell_h = cell_h or CELL_H
        self._crops: dict[tuple[int, int], RGBA] = {}

    @property
    def columns(self) -> int:
        return max(1, self.width // self.cell_w)

    @property
    def rows(self) -> int:
        return max(1, self.height // self.cell_h)

    def cell(self, col: int, row: int) -> RGBA:
        key = (col, row)
        cached = self._crops.get(key)
        if cached is None:
            cached = png.crop(
                self._image, col * self.cell_w, row * self.cell_h, self.cell_w, self.cell_h
            )
            self._crops[key] = cached
        return cached

    def at(self, index: int) -> RGBA:
        """Cell ``index`` in row-major order, wrapping around."""
        total = self.columns * self.rows
        index %= max(1, total)
        return self.cell(index % self.columns, index // self.columns)

    def frame(self, motion: Motion, index: int) -> RGBA:
        col, row = motion.cell(index % motion.frames)
        return self.cell(col, row)


class Pet:
    """A playing sprite: current motion, current frame, next frame deadline."""

    def __init__(self, sheet: Spritesheet, motion: str = "idle") -> None:
        self.sheet = sheet
        self.motion = MOTIONS[motion]
        self.index = 0
        self.started = time.monotonic()
        self._deadline = self.started + self.motion.delays[0] / 1000
        self._rows: dict[tuple[str, int, int, int, str, str], list[str]] = {}
        self._scaled: dict[tuple[str, int, int, int], RGBA] = {}

    def play(self, name: str) -> None:
        """Switch motion (ignored when unknown or already playing)."""
        motion = MOTIONS.get(name)
        if motion is None or motion is self.motion:
            return
        self.motion = motion
        self.index = 0
        self.started = time.monotonic()
        self._deadline = self.started + motion.delays[0] / 1000

    def advance(self, now: float | None = None) -> bool:
        """Move to the next frame if its delay has elapsed; True when it did."""
        if now is None:
            now = time.monotonic()
        if now < self._deadline:
            return False
        self.index = (self.index + 1) % self.motion.frames
        self._deadline = now + self.motion.delays[self.index] / 1000
        return True

    def pixels(self) -> RGBA:
        return self.sheet.frame(self.motion, self.index)

    def scaled(self, px_w: int, px_h: int) -> RGBA:
        key = (self.motion.name, self.index, px_w, px_h)
        cached = self._scaled.get(key)
        if cached is None:
            cached = png.scale(self.pixels(), px_w, px_h)
            self._scaled[key] = cached
        return cached

    def rows(
        self,
        max_cols: int,
        max_rows: int,
        *,
        mode: str = blocks.MODE_TRUE,
        backend: str = "blocks",
    ) -> list[str]:
        """Current frame as styled cell rows, cached per frame/box/backend.

        Quantising a few hundred pixels costs more than a frame's budget, and
        the result only changes when the frame, the box, the colour mode or
        the cell backend does, so every combination is memoised.
        """
        key = (self.motion.name, self.index, max_cols, max_rows, mode, backend)
        cached = self._rows.get(key)
        if cached is None:
            cached = graphics.render(self.pixels(), max_cols, max_rows, mode=mode, backend=backend)
            self._rows[key] = cached
        return cached

    def fit(self, max_cols: int, max_rows: int) -> tuple[int, int]:
        """Cells the current frame actually fills, for placement."""
        width, height, _ = self.pixels()
        return blocks.fit(width, height, max_cols, max_rows)


@dataclass(frozen=True)
class GifMotion:
    """The demo's title frame for a GIF: a name plus a human label."""

    name: str = "gif"
    label: str = ""


#: Steps in a GIF crossfade. Quantising the progress keeps the blended rows
#: cacheable: at 30 fps a 650 ms frame has ~20 ticks but only this many paints.
SMOOTH_STEPS = 12
#: Most of a GIF frame is shown crisp; only the tail crossfades into the next.
SMOOTH_FROM = 0.75
#: A gentle linear-light correction keeps the highlights of dark photographic
#: frames from being dragged down without crushing their shadow detail.
GIF_GAMMA = 1.4


def _blend(a: RGBA, b: RGBA, t: float) -> RGBA:
    """Linear blend of two same-sized RGBA buffers (no dependencies)."""
    width, height, left = a
    right = b[2]
    out = bytearray(width * height * 4)
    for i in range(0, width * height * 4, 4):
        for channel in range(4):
            x, y = left[i + channel], right[i + channel]
            out[i + channel] = x + round((y - x) * t)
    return width, height, bytes(out)


class GifPet:
    """An animated GIF played with the recorded inter-frame delays.

    Frames are decoded once up front. By default the gap between two frames is
    crossfaded in quantised steps, so a slow GIF (650 ms per frame) glides
    instead of jumping; press ``s`` in the demo for the original hard cuts.
    Both the scaled pixels and the styled rows are cached per frame and box.
    """

    def __init__(self, animation: Gif, label: str = "", *, smooth: bool = True) -> None:
        self.gif = animation
        self.motion = GifMotion(label=label or f"gif · {len(animation.frames)} frames")
        self.index = 0
        self.smooth = smooth
        self.started = time.monotonic()
        self._frame_start = self.started
        self._deadline = self.started + animation.delays[0] / 1000
        self._rows: dict[tuple[int, int, int, str, str, int], list[str]] = {}
        self._scaled: dict[tuple[int, int, int], RGBA] = {}

    def play(self, name: str) -> None:
        """GIFs have no motions; steering keys leave the timeline alone."""

    def advance(self, now: float | None = None) -> bool:
        if now is None:
            now = time.monotonic()
        if now < self._deadline:
            return False
        self.index = (self.index + 1) % len(self.gif.frames)
        self._frame_start = now
        self._deadline = now + self.gif.delays[self.index] / 1000
        return True

    def pixels(self) -> RGBA:
        return self.gif.frames[self.index][0]

    def scaled(self, px_w: int, px_h: int) -> RGBA:
        key = (self.index, px_w, px_h)
        cached = self._scaled.get(key)
        if cached is None:
            cached = png.scale(self.pixels(), px_w, px_h, gamma=GIF_GAMMA)
            self._scaled[key] = cached
        return cached

    def _step(self) -> int:
        if not self.smooth or len(self.gif.frames) < 2:
            return 0
        delay = self.gif.delays[self.index] / 1000
        if delay <= 0:
            return 0
        progress = (time.monotonic() - self._frame_start) / delay
        blend = max(0.0, min(1.0, (progress - SMOOTH_FROM) / (1 - SMOOTH_FROM)))
        return int(blend * SMOOTH_STEPS)

    def rows(
        self,
        max_cols: int,
        max_rows: int,
        *,
        mode: str = blocks.MODE_TRUE,
        backend: str = "blocks",
    ) -> list[str]:
        step = self._step()
        key = (self.index, max_cols, max_rows, mode, backend, step)
        cached = self._rows.get(key)
        if cached is None:
            width, height, _ = self.pixels()
            geometry = {"quadrants": graphics.quadrants.box, "braille": graphics.braille.box}.get(
                backend, blocks.box
            )
            box_w, box_h = geometry(width, height, max_cols, max_rows)
            pixels = png.scale(self.pixels(), box_w, box_h, gamma=GIF_GAMMA)
            if step:
                next_index = (self.index + 1) % len(self.gif.frames)
                pixels = _blend(
                    pixels,
                    png.scale(self.gif.frames[next_index][0], box_w, box_h, gamma=GIF_GAMMA),
                    step / SMOOTH_STEPS,
                )
            if backend == "quadrants":
                # Already scaled onto the quadrant subpixel grid above.
                cached = graphics.quadrants.rows(pixels, box_w, box_h, mode=mode)
            elif backend == "braille":
                cached = graphics.braille.rows(pixels, box_w, box_h, mode=mode)
            else:
                cached = graphics.render(pixels, max_cols, max_rows, mode=mode, backend=backend)
            self._rows[key] = cached
        return cached

    def fit(self, max_cols: int, max_rows: int) -> tuple[int, int]:
        width, height, _ = self.pixels()
        return blocks.fit(width, height, max_cols, max_rows)


@dataclass(frozen=True)
class StillMotion:
    """A single image has one 'motion', used only for the title."""

    name: str = "still"
    label: str = ""


class Still:
    """Any decoded image, played as one static frame."""

    def __init__(self, pixels: RGBA, label: str = "") -> None:
        self._pixels = pixels
        self.label = label or "image"
        self.motion = StillMotion(label=self.label)
        self._rows: dict[tuple[int, int, str, str], list[str]] = {}
        self._scaled: dict[tuple[int, int], RGBA] = {}

    def play(self, name: str) -> None:
        """No animations to switch; steering keys are harmless no-ops."""

    def advance(self, now: float | None = None) -> bool:
        return False

    def pixels(self) -> RGBA:
        return self._pixels

    def scaled(self, px_w: int, px_h: int) -> RGBA:
        key = (px_w, px_h)
        cached = self._scaled.get(key)
        if cached is None:
            cached = png.scale(self._pixels, px_w, px_h)
            self._scaled[key] = cached
        return cached

    def rows(
        self,
        max_cols: int,
        max_rows: int,
        *,
        mode: str = blocks.MODE_TRUE,
        backend: str = "blocks",
    ) -> list[str]:
        key = (max_cols, max_rows, mode, backend)
        cached = self._rows.get(key)
        if cached is None:
            cached = graphics.render(self._pixels, max_cols, max_rows, mode=mode, backend=backend)
            self._rows[key] = cached
        return cached

    def fit(self, max_cols: int, max_rows: int) -> tuple[int, int]:
        width, height, _ = self._pixels
        return blocks.fit(width, height, max_cols, max_rows)


def looks_like_sheet(pixels: RGBA, cell_w: int = CELL_W, cell_h: int = CELL_H) -> bool:
    """True for an atlas of at least 2x2 cells of the given size."""
    width, height, _ = pixels
    return (
        width >= cell_w * 2
        and height >= cell_h * 2
        and width % cell_w == 0
        and height // cell_h >= 2
    )


def key_flat_background(pixels: RGBA, *, tolerance: int = 32, mode: str = "flood") -> RGBA:
    """Make a flat dark atlas background transparent.

    ``flood`` only clears near-background pixels connected to the image edge,
    so dark hair, eyes and outlines in the interior survive. ``global`` is
    the old threshold (faster, but it can punch holes in dark artwork).
    ``off`` returns the image unchanged.
    """
    width, height, data = pixels
    if mode == "off" or any(a != 255 for a in data[3::4]):
        return pixels
    cr, cg, cb = data[0], data[1], data[2]
    if max(cr, cg, cb) > 24:
        return pixels
    out = bytearray(data)

    def near_bg(o: int) -> bool:
        return max(abs(data[o] - cr), abs(data[o + 1] - cg), abs(data[o + 2] - cb)) <= 8

    if mode == "global":
        for i in range(0, len(data), 4):
            diff = max(abs(data[i] - cr), abs(data[i + 1] - cg), abs(data[i + 2] - cb))
            if diff <= 8:
                out[i + 3] = 0
            elif diff < tolerance:
                out[i + 3] = round((diff - 8) / (tolerance - 8) * 255)
        return width, height, bytes(out)

    seen = bytearray(width * height)
    queue: deque[int] = deque()
    for x in range(width):
        queue.extend((x, (height - 1) * width + x))
    for y in range(height):
        queue.extend((y * width, y * width + width - 1))
    while queue:
        index = queue.popleft()
        if seen[index] or not near_bg(index * 4):
            continue
        seen[index] = 1
        out[index * 4 + 3] = 0
        y, x = divmod(index, width)
        if x:
            queue.append(index - 1)
        if x + 1 < width:
            queue.append(index + 1)
        if y:
            queue.append(index - width)
        if y + 1 < height:
            queue.append(index + width)
    # Soft edge: pixels just outside the flood get partial alpha instead of a
    # hard jagged cut, but only when they touch the cleared background.
    for index in range(width * height):
        if seen[index]:
            continue
        o = index * 4
        diff = max(abs(data[o] - cr), abs(data[o + 1] - cg), abs(data[o + 2] - cb))
        if diff >= tolerance:
            continue
        y, x = divmod(index, width)
        neighbours = (
            (index - 1 if x else -1),
            (index + 1 if x + 1 < width else -1),
            (index - width if y else -1),
            (index + width if y + 1 < height else -1),
        )
        if any(n >= 0 and seen[n] for n in neighbours):
            out[o + 3] = round((diff - 8) / (tolerance - 8) * 255)
    return width, height, bytes(out)


@dataclass(frozen=True)
class GenericMotion:
    """A cell position, used as the title label of a generic atlas."""

    name: str = "sheet"
    label: str = ""


class GenericPet:
    """Cycle the non-empty cells of an arbitrary atlas as one animation.

    Community sheets do not follow the Codex v2 motion rows, so there are no
    named motions or published delays — just frames in row-major order. The
    sequence ping-pongs by default so the loop point has no hard cut.
    """

    def __init__(
        self,
        sheet: Spritesheet,
        *,
        delay: float = 0.25,
        start: int = 0,
        count: int | None = None,
        pingpong: bool = True,
    ) -> None:
        self.sheet = sheet
        self.delay = max(0.02, delay)
        total = max(1, sheet.columns * sheet.rows)
        candidates = [(start + i) % total for i in range(min(count or total, total))]
        # An empty atlas cell would transmit a fully transparent frame, which
        # looks like a one-frame blink. Skip cells with no visible pixels.
        self.indices = [
            index for index in candidates if any(a >= 32 for a in sheet.at(index)[2][3::4])
        ] or candidates
        self.sequence = (
            self.indices + self.indices[-2:0:-1]
            if pingpong and len(self.indices) > 2
            else list(self.indices)
        )
        self.index = self.sequence[0]
        self._step = 0
        self._deadline = time.monotonic() + self.delay
        self._rows: dict[tuple[int, int, int, str, str], list[str]] = {}
        self._scaled: dict[tuple[int, int, int], RGBA] = {}
        self._label()

    def _label(self) -> None:
        col, row = self.index % self.sheet.columns, self.index // self.sheet.columns
        self.motion = GenericMotion(
            label=f"pose {self._step + 1}/{len(self.sequence)} · cell {col},{row}"
        )

    def play(self, name: str) -> None:
        """No named motions; keys are harmless no-ops."""

    def advance(self, now: float | None = None) -> bool:
        if now is None:
            now = time.monotonic()
        if now < self._deadline:
            return False
        self._step = (self._step + 1) % len(self.sequence)
        self.index = self.sequence[self._step]
        self._deadline = now + self.delay
        self._label()
        return True

    def pixels(self) -> RGBA:
        return self.sheet.at(self.index)

    def scaled(self, px_w: int, px_h: int) -> RGBA:
        key = (self.index, px_w, px_h)
        cached = self._scaled.get(key)
        if cached is None:
            cached = png.scale(self.pixels(), px_w, px_h)
            self._scaled[key] = cached
        return cached

    def rows(
        self,
        max_cols: int,
        max_rows: int,
        *,
        mode: str = blocks.MODE_TRUE,
        backend: str = "blocks",
    ) -> list[str]:
        key = (self.index, max_cols, max_rows, mode, backend)
        cached = self._rows.get(key)
        if cached is None:
            cached = graphics.render(self.pixels(), max_cols, max_rows, mode=mode, backend=backend)
            self._rows[key] = cached
        return cached

    def fit(self, max_cols: int, max_rows: int) -> tuple[int, int]:
        width, height, _ = self.pixels()
        return blocks.fit(width, height, max_cols, max_rows)
