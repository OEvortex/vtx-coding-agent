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
from dataclasses import dataclass
from pathlib import Path

from vx.tui.graphics import blocks, png
from vx.tui.graphics.png import RGBA

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

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._image = png.load(str(self.path))
        self.width, self.height, _ = self._image
        self._crops: dict[tuple[int, int], RGBA] = {}

    @property
    def columns(self) -> int:
        return max(1, self.width // CELL_W)

    @property
    def rows(self) -> int:
        return max(1, self.height // CELL_H)

    def cell(self, col: int, row: int) -> RGBA:
        key = (col, row)
        cached = self._crops.get(key)
        if cached is None:
            cached = png.crop(self._image, col * CELL_W, row * CELL_H, CELL_W, CELL_H)
            self._crops[key] = cached
        return cached

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
        self._rows: dict[tuple[str, int, int, int, str], list[str]] = {}
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

    def rows(self, max_cols: int, max_rows: int, *, mode: str = blocks.MODE_TRUE) -> list[str]:
        """Current frame as half-block rows, cached per (frame, box, colours).

        Quantising a few hundred pixels costs more than a frame's budget, and
        the result only changes when the frame, the box or the colour mode
        does, so every combination is memoised.
        """
        key = (self.motion.name, self.index, max_cols, max_rows, mode)
        cached = self._rows.get(key)
        if cached is None:
            cached = blocks.render(self.pixels(), max_cols, max_rows, mode=mode)
            self._rows[key] = cached
        return cached

    def fit(self, max_cols: int, max_rows: int) -> tuple[int, int]:
        """Cells the current frame actually fills, for placement."""
        width, height, _ = self.pixels()
        return blocks.fit(width, height, max_cols, max_rows)
