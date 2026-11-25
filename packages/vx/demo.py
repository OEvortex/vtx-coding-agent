"""Hachiware as a terminal pet — the graphics prototype end to end.

Run it from this directory:

    cd packages/vx && uv run python demo.py

It needs a chiikawa-dots-pets spritesheet. Point ``VTX_PET_SHEET`` at the PNG
(or install the pet into ``~/.codex/pets/hachiware/``) and it animates with the
frame timings recorded in the released GIFs.

Three things are being exercised here:

* ``vx.tui.graphics.blocks`` — half blocks, one cell = two pixels. Works in
  VS Code, GNOME Terminal, tmux, everywhere. This is what you will see unless
  your terminal speaks sixel or the kitty graphics protocol.
* ``vx.tui.graphics.protocols`` — the same pet drawn as real pixels, chosen by
  the capability probe. Press ``g`` to cycle backends by hand.
* ``vx.pet`` — sprite-sheet playback, so a "block" is just a block that owns a
  ``Pet`` and asks it for rows.

Keys: ``←``/``→`` run left/right · ``space`` next motion · ``1``-``9``/``0`` jump
to a motion · ``g`` cycle backend · ``p`` pause · ``+``/``-`` resize · ``w``
wander · ``q`` quit.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from vx.pet import ORDER, Pet, Spritesheet, find_sheet
from vx.tui import ansi, graphics, keys, theme
from vx.tui.canvas import Painter
from vx.tui.graphics import blocks
from vx.tui.terminal import Terminal

FPS = 30
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
    parser.add_argument(
        "--gfx",
        default=os.environ.get("VTX_GFX", "auto"),
        choices=("auto", "blocks", "sixel", "kitty", "iterm"),
        help="force a graphics backend",
    )
    return parser.parse_args(argv)


class Scene:
    """Places the pet in the frame and paints either cells or real pixels."""

    def __init__(self, term: Terminal, pet: Pet, caps: graphics.Caps, size: int | None) -> None:
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

    # -- story -------------------------------------------------------------

    def update(self, now: float, cols: int) -> None:
        """Advance the sprite and, unless the user is steering, the motion."""
        if self.paused:
            return
        self.pet.advance(now)
        if self.manual:
            if now < self.hold_until:
                return
            self.manual = False
        if self.wander:
            self.scripted(now, max(0, cols - self.width(cols)))

    def scripted(self, now: float, room: int) -> None:
        """Pick motions on a timer; running hooks slide the pet across."""
        name = self.pet.motion.name
        if name in RUN:
            self.x += 0.7 * self.direction
            if self.x <= 0 or self.x >= room:
                self.x = min(max(self.x, 0.0), room)
                self.direction *= -1
                self.pet.play("running-left" if self.direction < 0 else "running-right")
            return
        if now < self._next_motion:
            return
        self._script = (self._script + 1) % len(SCRIPT)
        name = SCRIPT[self._script]
        if name in RUN:
            self.direction = 1 if name == "running-right" else -1
            self.x = 0.0 if self.direction > 0 else room
        self.pet.play(name)
        self._next_motion = now + 3.0

    def steer(self, name: str) -> None:
        self.pet.play(name)
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

        title = (
            f"{theme.current().tag_style}hachiware{ansi.RESET}"
            f"{theme.current().dim_style} · {self.pet.motion.label or self.pet.motion.name}"
            f" · {art_w}x{art_h} cells · {self.backend}"
            f" · colour {self.mode}"
            f" · sixel={'yes' if self.caps.sixel else 'no'}"
            f" kitty={'yes' if self.caps.kitty else 'no'}{ansi.RESET}"
        )
        out = [ansi.clip(title, cols), ""]
        # Pixel backends draw over blank cells, so only the boxes matter here.
        art = (
            self.pet.rows(art_w, art_h, mode=self.mode)
            if self.backend == "blocks"
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
        if self.backend != "blocks":
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
                body = graphics.protocols.kitty_delete(1) + move + body
        return f"{move}{body}{hide}" if body else ""

    def help_rows(self, cols: int) -> list[str]:
        dim = theme.current().dim_style
        hint = (
            "←/→ run · space next · 1-9,0 pick · g backend · t colour · p pause · "
            "+/- size · w wander · q quit"
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
        elif event in ("g",):
            self.cycle_backend()
        elif event in ("t",):
            self.mode = blocks.MODE_256 if self.mode == blocks.MODE_TRUE else blocks.MODE_TRUE
        elif event in ("left", "right"):
            self.steer("running-left" if event == "left" else "running-right")
        elif event in ("space", "n"):
            self.steer(ORDER[(ORDER.index(self.pet.motion.name) + 1) % len(ORDER)])
        elif event.isdigit():
            index = (int(event) - 1) % 10
            if index < len(ORDER):
                self.steer(ORDER[index])
        return True

    def cycle_backend(self) -> None:
        options = list(self.caps.backends)
        if "blocks" not in options:
            options.append("blocks")
        position = options.index(self.backend) if self.backend in options else 0
        self.backend = options[(position + 1) % len(options)]
        self.term.write(graphics.protocols.kitty_delete(1))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    size = None if str(args.size).lower() == "auto" else int(args.size)
    sheet_path = find_sheet(args.pet)
    if sheet_path is None:
        print(
            "no spritesheet found. Set VTX_PET_SHEET to the pet's spritesheet.png,\n"
            "or install it under ~/.codex/pets/<pet>/spritesheet.png.",
            file=sys.stderr,
        )
        return 2

    theme.apply_theme()
    sheet = Spritesheet(sheet_path)
    pet = Pet(sheet)
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
            scene.update(now, cols)
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
