"""Hachiware as a terminal pet — the graphics prototype end to end.

Run it from this directory:

    cd packages/vx && uv run python demo.py

Point ``VTX_PET_SHEET`` at a Codex v2 spritesheet (8x11 cells of 192x208, from
``codex-anime-pets`` or chiikawa-dots-pets) and it animates with the recorded
frame timings. A real ``.gif`` works too: its frames are decoded once and played
at the delays stored inside the file. Any other image — PNG, WebP, JPEG — is
shown as a single static frame; WebP/JPEG are decoded through ``ffmpeg`` if
available (the TUI itself stays stdlib-only). ``--still`` forces single-image
mode even for a sheet. Community atlases with a different cell size work via
``--cell WxH``; their cells cycle at ``--fps`` (default 2.5 — this is pose
speed, not image quality), ping-ponging so the loop has no hard cut. ``--row N``
animates one motion row as a sequence.

Three things are being exercised here:

* ``vx.tui.graphics.blocks`` — half blocks, one cell = two pixels. Works in
  VS Code, GNOME Terminal, tmux, everywhere. This is what you will see unless
  your terminal speaks sixel or the kitty graphics protocol.
* ``vx.tui.graphics.quadrants`` — four pixels per cell, twice the horizontal
  resolution for fonts that draw the quadrant block glyphs cleanly.
* ``vx.tui.graphics.braille`` — eight dots (2x4) per cell, the sharpest
  text-only path; needs a font with braille glyphs (FreeMono is a safe fallback).
* ``vx.tui.graphics.protocols`` — the same pet drawn as real pixels, chosen by
  the capability probe (DA1 sixel, ``CSI ?u`` kitty, env for iTerm2). Press
  ``g`` to cycle backends by hand. In VS Code, turn on
  ``terminal.integrated.enableImages`` so DA1 advertises sixel, then this is
  picked automatically; ``TERM=xterm-sixel`` is a fallback hint.
* ``vx.pet`` — sprite-sheet playback, so a "block" is just a block that owns a
  ``Pet`` and asks it for rows.

Keys: ``←``/``→`` run left/right · ``space`` next motion · ``1``-``9``/``0`` jump
to a motion · ``g`` cycle backend · ``t`` colour · ``s`` GIF crossfade · ``p``
pause · ``+``/``-`` resize · ``w`` wander · ``q`` quit.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

from vx.pet import (
    ORDER,
    GenericPet,
    GifPet,
    Pet,
    Player,
    Spritesheet,
    Still,
    find_sheet,
    key_flat_background,
    looks_like_sheet,
)
from vx.tui import ansi, graphics, keys, theme
from vx.tui.canvas import Painter
from vx.tui.graphics import blocks, gif, png
from vx.tui.terminal import Terminal

FPS = 30
#: Cells per second while running. Time-based so the pet does not speed up or
#: stagger when a frame takes longer than the 30 fps budget.
WALK_SPEED = 21.0
#: Motion sequence the pet follows on its own when it is not being steered.
SCRIPT = (
    "idle",
    "waiting",
    "waving",
    "jumping",
    "review",
    "running",
    "running-right",
    "failed",
    "look",
)
RUN = ("running-right", "running-left")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="animate a terminal pet")
    parser.add_argument(
        "--size",
        default="auto",
        help="pet width in cells, or 'auto' to fill the terminal (default)",
    )
    parser.add_argument("--pet", default="hachiware", help="pet directory name")
    parser.add_argument("--still", action="store_true", help="show the image as one static frame")
    parser.add_argument(
        "--cell", default=None, help="atlas cell size, e.g. 108x108 (generic sheet mode)"
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=2.5,
        help="pose cycle speed for generic sheets, not render quality (default 2.5)",
    )
    parser.add_argument(
        "--start", type=int, default=0, help="first cell of a generic sheet (default 0)"
    )
    parser.add_argument(
        "--count", type=int, default=0, help="number of cells to cycle (default: all)"
    )
    parser.add_argument("--row", type=int, default=None, help="animate one row of a generic sheet")
    parser.add_argument(
        "--no-pingpong", action="store_true", help="loop straight back to the first frame"
    )
    parser.add_argument(
        "--key",
        choices=("flood", "global", "off"),
        default="flood",
        help="how to remove a flat dark atlas background (default: flood)",
    )
    parser.add_argument(
        "--gfx",
        default=os.environ.get("VTX_GFX", "auto"),
        choices=("auto", "blocks", "quadrants", "braille", "sixel", "kitty", "iterm"),
        help="force a graphics backend",
    )
    return parser.parse_args(argv)


def load_image(path) -> tuple[int, int, bytes]:
    """Decode PNG directly; hand other formats to ffmpeg as a PNG pipe.

    The graphics package itself is stdlib-only. This is an optional bridge so
    a WebP sheet or a JPEG screenshot can be tested without adding a Python
    dependency; only non-PNG files need the external tool.
    """
    if path.suffix.lower() == ".png":
        return png.load(str(path))
    command = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"cannot decode {path}: install ffmpeg or use a PNG ({exc})") from exc
    return png.decode(result.stdout)


def parse_cell(value: str) -> tuple[int, int]:
    try:
        w, h = (int(part) for part in value.lower().replace("x", ":").split(":"))
    except ValueError:
        raise SystemExit(f"bad --cell {value!r}; use WxH, e.g. 108x108") from None
    if w < 2 or h < 2:
        raise SystemExit("--cell dimensions must be at least 2")
    return w, h


class Scene:
    """Places the pet in the frame and paints either cells or real pixels."""

    def __init__(self, term: Terminal, pet: Player, caps: graphics.Caps, size: int | None) -> None:
        self.term = term
        self.pet = pet
        self.caps = caps
        # None means "as big as the terminal allows": shrinking the art
        # further deletes its line work, so the default is full size.
        self.size = None if size is None else max(6, size)
        self.backend = caps.best()
        self.mode = blocks.MODE_TRUE if blocks.supports_truecolor() else blocks.MODE_256
        self.x = 0.0
        self.direction = 1
        self._script = 0
        self._next_motion = time.monotonic() + 3.0
        self.hold_until = 0.0
        self.manual = False
        self.paused = False
        self.wander = True
        self._last_move: float | None = None
        self._last_payload: tuple[int, int, int, int] | None = None

    # -- story -------------------------------------------------------------

    def update(self, now: float, rows: int, cols: int) -> None:
        """Advance the sprite and, unless the user is steering, the motion."""
        if self.paused:
            return
        self.pet.advance(now)
        room = max(0, cols - self.geometry(rows, cols)[0])
        if self.manual:
            if now < self.hold_until:
                # A steered run must keep travelling; only the automatic
                # motion picker takes a break while the key is held.
                if self.pet.motion.name in RUN:
                    self.slide(room, now)
                return
            self.manual = False
        if self.wander:
            self.scripted(now, room)

    def slide(self, room: int, now: float) -> None:
        """Move one frame's worth of cells, bouncing at the edges."""
        dt = 0.0 if self._last_move is None else min(0.1, max(0.0, now - self._last_move))
        self._last_move = now
        self.x += WALK_SPEED * dt * self.direction
        if room <= 0:
            # No horizontal room: run in place instead of flipping direction
            # (and restarting the animation) on every single frame.
            self.x = 0.0
            return
        # Only bounce when this step actually crosses an edge. A run that
        # starts at x=0 heading right must not flip on its very first frame.
        if (self.direction > 0 and self.x >= room) or (self.direction < 0 and self.x <= 0):
            self.x = min(max(self.x, 0.0), room)
            self.direction *= -1
            self.pet.play("running-left" if self.direction < 0 else "running-right")

    def scripted(self, now: float, room: int) -> None:
        """Pick motions on a timer; running hooks slide the pet across."""
        name = self.pet.motion.name
        if name in RUN:
            self.slide(room, now)
            return
        if now < self._next_motion:
            return
        self._script = (self._script + 1) % len(SCRIPT)
        name = SCRIPT[self._script]
        if name in RUN:
            self.direction = 1 if name == "running-right" else -1
            self.x = 0.0 if self.direction > 0 else room
            self._last_move = now
        self.pet.play(name)
        self._next_motion = now + 3.0

    def steer(self, name: str) -> None:
        self.pet.play(name)
        if name in RUN:
            rows, cols = self.term.size()
            room = max(0, cols - self.geometry(rows, cols)[0])
            self.direction = 1 if name == "running-right" else -1
            self.x = 0.0 if self.direction > 0 else float(room)
            self._last_move = None
        self.manual = True
        self.hold_until = time.monotonic() + 4.0

    # -- geometry ----------------------------------------------------------

    def width(self, cols: int) -> int:
        """Pet width in cells, never wider than the frame it is drawn into."""
        return max(4, cols if self.size is None else min(self.size, cols))

    def geometry(self, rows: int, cols: int) -> tuple[int, int]:
        """Cells the pet fills: width first, capped so the hint row survives."""
        return self.pet.fit(self.width(cols), max(1, rows - 6))

    def place(self, room: int) -> int:
        """Left cell of the pet, nudged by the wander offset."""
        return int(min(max(self.x, 0.0), max(0, room)))

    # -- frame -------------------------------------------------------------

    def frame(self, rows: int, cols: int) -> tuple[list[str], str]:
        """Rows for the painter, plus any pixel payload to draw afterwards."""
        art_w, art_h = self.geometry(rows, cols)
        top = 2
        left = self.place(cols - art_w)

        name = getattr(self.pet, "label", "hachiware")
        title = (
            f"{theme.current().tag_style}{name}{ansi.RESET}"
            f"{theme.current().dim_style} · {self.pet.motion.label or self.pet.motion.name}"
            f" · {art_w}x{art_h} cells · {self.backend}"
            f" · colour {self.mode}"
            f" · sixel={'yes' if self.caps.sixel else 'no'}"
            f" kitty={'yes' if self.caps.kitty else 'no'}{ansi.RESET}"
        )
        out = [ansi.clip(title, cols), ""]
        # Pixel backends draw over blank cells, so only the boxes matter here.
        cell_backend = self.backend in ("blocks", "quadrants", "braille")
        art = (
            self.pet.rows(art_w, art_h, mode=self.mode, backend=self.backend)
            if cell_backend
            else [""] * art_h
        )
        shape = art + [""] * (art_h - len(art))
        # Clip here, not in the terminal: a row wider than the frame wraps
        # and shreds the picture into shifted bands.
        out.extend(ansi.clip(" " * left + row, cols) for row in shape)
        out.append("")
        out.extend(self.help_rows(cols))
        out.extend("" for _ in range(max(0, rows - len(out))))
        payload = ""
        if not cell_backend:
            payload = self.payload(art_w, art_h, top, left)
        return out[:rows], payload

    def payload(self, art_w: int, art_h: int, top: int, left: int) -> str:
        kind = self.backend
        move = f"\x1b[{top + 1};{left + 1}H"
        hide = "\x1b[?25l"
        if kind == "sixel":
            px_w, px_h = self.caps.pixel_box(art_w, art_h)
            body = graphics.protocols.sixel(self.pet.scaled(px_w, px_h))
        else:
            body = graphics.protocols.paint(kind, self.pet.pixels(), art_w, art_h, image_id=1)
            if kind == "kitty":
                # Deleting and recreating the image on every frame is what
                # makes kitty flicker. Only do it when the box actually moves;
                # a new transmission with the same id replaces it in place.
                position = (art_w, art_h, top, left)
                if position != self._last_payload:
                    body = graphics.protocols.kitty_delete(1) + move + body
                self._last_payload = position
        return f"{move}{body}{hide}" if body else ""

    def help_rows(self, cols: int) -> list[str]:
        dim = theme.current().dim_style
        hint = (
            "←/→ run · space next · 1-9,0 pick · g backend (blocks/quadrants/braille) · "
            "t colour · s smooth · p pause · +/- size · w wander · q quit"
        )
        return ansi.wrap(f"{dim}{hint}{ansi.RESET}", cols)

    # -- input -------------------------------------------------------------

    def key(self, event: str) -> bool:
        """Handle one key; False means quit."""
        if event in ("q", "ctrl+c", "esc"):
            return False
        if event in ("p",):
            self.paused = not self.paused
        elif event in ("+", "="):
            self.size = min(self.term.size()[1], self.width(self.term.size()[1]) + 2)
        elif event in ("-", "_"):
            self.size = max(6, self.width(self.term.size()[1]) - 2)
        elif event in ("w",):
            self.wander = not self.wander
        elif event in ("s",) and isinstance(self.pet, GifPet):
            self.pet.smooth = not self.pet.smooth
        elif event in ("g",):
            self.cycle_backend()
        elif event in ("t",):
            self.mode = blocks.MODE_256 if self.mode == blocks.MODE_TRUE else blocks.MODE_TRUE
        elif event in ("left", "right"):
            self.steer("running-left" if event == "left" else "running-right")
        elif event in ("space", "n"):
            if self.pet.motion.name in ORDER:
                self.steer(ORDER[(ORDER.index(self.pet.motion.name) + 1) % len(ORDER)])
        elif event.isdigit():
            index = (int(event) - 1) % 10
            if self.pet.motion.name in ORDER and index < len(ORDER):
                self.steer(ORDER[index])
        return True

    def cycle_backend(self) -> None:
        options = list(self.caps.cycle_backends)
        position = options.index(self.backend) if self.backend in options else 0
        self.backend = options[(position + 1) % len(options)]
        self._last_payload = None
        self.term.write(graphics.protocols.kitty_delete(1))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    size = None if str(args.size).lower() == "auto" else int(args.size)
    sheet_path = find_sheet(args.pet)
    if sheet_path is None:
        print(
            "no image found. Set VTX_PET_SHEET to a PNG/WebP/JPEG sheet or any image,\n"
            "or install a pet under ~/.codex/pets/<pet>/spritesheet.png.",
            file=sys.stderr,
        )
        return 2

    theme.apply_theme()
    if args.cell:
        cw, ch = parse_cell(args.cell)
        sheet = Spritesheet(key_flat_background(load_image(sheet_path), mode=args.key), cw, ch)
        start, count = args.start, args.count or None
        if args.row is not None:
            start, count = args.row * sheet.columns, sheet.columns
        pet = GenericPet(
            sheet,
            delay=1 / max(0.5, args.fps),
            start=start,
            count=count,
            pingpong=not args.no_pingpong,
        )
    elif sheet_path.suffix.lower() == ".gif":
        animation = gif.load(sheet_path)
        if args.still:
            pet = Still(animation.frames[0][0], sheet_path.stem)
        else:
            pet = GifPet(animation, f"{sheet_path.stem} · {len(animation.frames)} frames")
    else:
        image = load_image(sheet_path)
        if args.still or not looks_like_sheet(image):
            if not args.still:
                print(
                    f"showing {sheet_path.name} as one still image; "
                    "if it is an atlas, pass --cell WxH to animate it",
                    file=sys.stderr,
                )
            pet = Still(image, sheet_path.stem)
        else:
            pet = Pet(Spritesheet(image))
    decoder = keys.KeyDecoder()
    painter = Painter()

    with Terminal() as term:
        caps = graphics.detect(term)
        if args.gfx != "auto":
            caps.sixel = args.gfx == "sixel"
            caps.kitty = args.gfx == "kitty"
            caps.iterm = args.gfx == "iterm"
            caps.source = "flag"
        scene = Scene(term, pet, caps, size)
        scene.backend = args.gfx if args.gfx != "auto" else caps.best()

        running = True
        while running:
            rows, cols = term.size()
            now = time.monotonic()
            scene.update(now, rows, cols)
            for event in decoder.feed(term.read_available()):
                running = scene.key(event)
                if not running:
                    break
            if not running:
                break
            frame_rows, payload = scene.frame(rows, cols)
            term.write(painter.paint(frame_rows, cols) + payload)
            budget = 1 / FPS - (time.monotonic() - now)
            if budget > 0:
                time.sleep(budget)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
