"""Take a PNG snapshot of an interactive terminal app.

The command runs in a pty at a fixed size, its output is recorded, then replayed
through a small terminal emulator (cursor moves, SGR colours, half blocks) and
painted into an image. This is the quick way to see what a TUI actually draws
when there is no human in front of the terminal.

    uv run python scripts/tui_snapshot.py --wait 3 --out snap.png -- uv run python demo.py

An existing recording can be replayed instead:

    uv run python scripts/tui_snapshot.py --from-raw snap.raw --out snap.png

Requires Pillow for image output (already in the dev environment).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import termios
import time
from dataclasses import dataclass

from PIL import Image, ImageDraw

DEFAULT_FG = (205, 214, 244)
DEFAULT_BG = (24, 24, 30)
BASIC = (
    (0, 0, 0),
    (170, 0, 0),
    (0, 170, 0),
    (170, 85, 0),
    (0, 0, 170),
    (170, 0, 170),
    (0, 170, 170),
    (170, 170, 170),
)
BRIGHT = (
    (85, 85, 85),
    (255, 85, 85),
    (85, 255, 85),
    (255, 255, 85),
    (85, 85, 255),
    (255, 85, 255),
    (85, 255, 255),
    (255, 255, 255),
)
_WHICH = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz@\\]^_`{|}~"

#: Quadrant block glyphs (U+2596-U+259F) as TL/TR/BL/BR bit masks, used by the
#: quadrant graphics backend. Without these branches they would be drawn as
#: text with the default font and show up as tofu boxes in the snapshot.
_QUAD_MASKS = {
    "▀": 0b1100,
    "▄": 0b0011,
    "▌": 0b1010,
    "▐": 0b0101,
    "▘": 0b1000,
    "▝": 0b0100,
    "▖": 0b0010,
    "▗": 0b0001,
    "▚": 0b1001,
    "▞": 0b0110,
    "▛": 0b1110,
    "▜": 0b1101,
    "▙": 0b1011,
    "▟": 0b0111,
    "█": 0b1111,
}

#: Braille dot bits, by (row, column) inside a cell (U+2800 base).
_BRAILLE_DOTS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))


def xterm256(index: int) -> tuple[int, int, int]:
    if index < 16:
        base = (0, 128, 255)
        return (base[(index >> 2) & 1], base[(index >> 1) & 1], base[index & 1])
    if index < 232:
        index -= 16
        r, index = divmod(index, 36)
        g, b = divmod(index, 6)
        return (55 + r * 40, 55 + g * 40, 55 + b * 40)
    level = 8 + (index - 232) * 10
    return (level, level, level)


@dataclass
class Cell:
    fg: tuple[int, int, int] = DEFAULT_FG
    bg: tuple[int, int, int] = DEFAULT_BG
    ch: str = " "


class Screen:
    """Minimal ANSI terminal: enough to reproduce a styled TUI frame."""

    def __init__(self, rows: int, cols: int) -> None:
        self.rows, self.cols = rows, cols
        self.grid = [[Cell() for _ in range(cols)] for _ in range(rows)]
        self.cy = self.cx = 0
        self.fg, self.bg = DEFAULT_FG, DEFAULT_BG
        self.bold = False

    def feed(self, text: str) -> None:
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\x1b":
                if i + 1 >= len(text):
                    return  # a write cut off mid-escape: drop the partial tail
                nxt = text[i + 1]
                if nxt == "[":
                    end = i + 2
                    while end < len(text) and text[end] not in _WHICH:
                        end += 1
                    if end >= len(text):
                        return
                    self.csi(text[i + 2 : end], text[end])
                    i = end + 1
                    continue
                if nxt in ("]", "P", "X", "^", "_"):
                    # OSC/DCS/APC: skip to BEL or ST. Sixels and images are
                    # deliberately not rasterised; use --raw to inspect them.
                    end = i + 2
                    while end < len(text):
                        if text[end] == "\x07":
                            end += 1
                            break
                        if text[end] == "\x1b" and end + 1 < len(text) and text[end + 1] == "\\":
                            end += 2
                            break
                        end += 1
                    i = end
                    continue
                i += 2
                continue
            if ch == "\r":
                self.cx = 0
            elif ch == "\n":
                self.cy = min(self.rows - 1, self.cy + 1)
            elif ch == "\b":
                self.cx = max(0, self.cx - 1)
            else:
                self.put(ch)
            i += 1

    def put(self, ch: str) -> None:
        if self.cx >= self.cols:
            self.cx, self.cy = 0, min(self.rows - 1, self.cy + 1)
        self.grid[self.cy][self.cx] = Cell(self.fg, self.bg, ch)
        self.cx += 1

    def csi(self, params: str, final: str) -> None:
        private = params.startswith("?")
        nums = [int(p) for p in re.split(r"[;:]", params.lstrip("?")) if p.isdigit()]
        arg = nums[0] if nums else 1
        if final in ("H", "f"):
            self.cy = max(0, (nums[0] if nums else 1) - 1)
            self.cx = max(0, (nums[1] if len(nums) > 1 else 1) - 1)
        elif final == "A":
            self.cy = max(0, self.cy - arg)
        elif final == "B":
            self.cy = min(self.rows - 1, self.cy + arg)
        elif final == "C":
            self.cx = min(self.cols - 1, self.cx + arg)
        elif final == "D":
            self.cx = max(0, self.cx - arg)
        elif final == "d":
            self.cy = max(0, arg - 1)
        elif final in ("G", "`"):
            self.cx = max(0, arg - 1)
        elif final == "K":
            mode = nums[0] if nums else 0
            loose = (
                range(self.cx, self.cols)
                if mode == 0
                else (range(0, self.cx + 1) if mode == 1 else range(self.cols))
            )
            for x in loose:
                self.grid[self.cy][x] = Cell(self.fg, self.bg, " ")
        elif final == "J":
            mode = nums[0] if nums else 0
            ys = (
                range(self.cy, self.rows)
                if mode == 0
                else (range(0, self.cy + 1) if mode == 1 else range(self.rows))
            )
            for y in ys:
                for x in range(self.cols):
                    self.grid[y][x] = Cell(self.fg, self.bg, " ")
        elif final == "m":
            self.sgr(nums)
        elif private and final == "h" and 1049 in nums:
            self.grid = [[Cell() for _ in range(self.cols)] for _ in range(self.rows)]

    def sgr(self, codes: list[int]) -> None:
        i = 0
        while i < len(codes):
            code = codes[i]
            if code == 0:
                self.fg, self.bg, self.bold = DEFAULT_FG, DEFAULT_BG, False
            elif code == 1:
                self.bold = True
            elif code == 22:
                self.bold = False
            elif 30 <= code <= 37:
                self.fg = BRIGHT[code - 30] if self.bold else BASIC[code - 30]
            elif 90 <= code <= 97:
                self.fg = BRIGHT[code - 90]
            elif 40 <= code <= 47:
                self.bg = BASIC[code - 40]
            elif 100 <= code <= 107:
                self.bg = BRIGHT[code - 100]
            elif code in (38, 48) and i + 1 < len(codes):
                if codes[i + 1] == 5 and i + 2 < len(codes):
                    colour = xterm256(codes[i + 2])
                    i += 2
                elif codes[i + 1] == 2 and i + 4 < len(codes):
                    colour = (codes[i + 2], codes[i + 3], codes[i + 4])
                    i += 4
                else:
                    i += 1
                    continue
                if code == 38:
                    self.fg = colour
                else:
                    self.bg = colour
            elif code == 39:
                self.fg = DEFAULT_FG
            elif code == 49:
                self.bg = DEFAULT_BG
            i += 1


def raster(screen: Screen, cell_w: int, cell_h: int) -> Image.Image:
    out = Image.new("RGB", (screen.cols * cell_w, screen.rows * cell_h), DEFAULT_BG)
    draw = ImageDraw.Draw(out)
    for y, row in enumerate(screen.grid):
        for x, cell in enumerate(row):
            ox, oy = x * cell_w, y * cell_h
            draw.rectangle([ox, oy, ox + cell_w - 1, oy + cell_h - 1], fill=cell.bg)
            if cell.ch == "▀":
                draw.rectangle([ox, oy, ox + cell_w - 1, oy + cell_h // 2 - 1], fill=cell.fg)
            elif cell.ch == "▄":
                draw.rectangle(
                    [ox, oy + cell_h // 2, ox + cell_w - 1, oy + cell_h - 1], fill=cell.fg
                )
            elif cell.ch in _QUAD_MASKS:
                mask = _QUAD_MASKS[cell.ch]
                hw, hh = cell_w // 2, cell_h // 2
                for bit, bx, by, bw, bh in (
                    (8, 0, 0, hw, hh),
                    (4, hw, 0, cell_w - hw, hh),
                    (2, 0, hh, hw, cell_h - hh),
                    (1, hw, hh, cell_w - hw, cell_h - hh),
                ):
                    if mask & bit:
                        draw.rectangle(
                            [ox + bx, oy + by, ox + bx + bw - 1, oy + by + bh - 1], fill=cell.fg
                        )
            elif 0x2800 <= ord(cell.ch) <= 0x28FF:
                mask = ord(cell.ch) - 0x2800
                bw, bh = cell_w // 2, cell_h // 4
                for row, dots in enumerate(_BRAILLE_DOTS):
                    for col, bit in enumerate(dots):
                        if mask & bit:
                            draw.rectangle(
                                [
                                    ox + col * bw + 1,
                                    oy + row * bh + 1,
                                    ox + (col + 1) * bw - 1,
                                    oy + (row + 1) * bh - 1,
                                ],
                                fill=cell.fg,
                            )
            elif cell.ch != " ":
                draw.text((ox, oy), cell.ch, fill=cell.fg)
    return out


def capture(
    command: list[str], cols: int, rows: int, wait: float, keys: str, cwd: str | None
) -> bytes:
    # Two ptys, not one: a TUI that sets stdin non-blocking (this one does)
    # would otherwise make stdout non-blocking too and silently drop the tail
    # of every multi-kilobyte paint, leaving broken escape sequences behind.
    in_master, in_slave = pty.openpty()
    out_master, out_slave = pty.openpty()
    size = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(in_slave, termios.TIOCSWINSZ, size)
    fcntl.ioctl(out_slave, termios.TIOCSWINSZ, size)
    env = os.environ.copy()
    env["COLUMNS"], env["LINES"] = str(cols), str(rows)
    proc = subprocess.Popen(
        command,
        stdin=in_slave,
        stdout=out_slave,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=cwd,
        close_fds=True,
    )
    os.close(in_slave)
    os.close(out_slave)
    chunks: list[bytes] = []
    start = time.monotonic()
    sent = False

    def drain(deadline: float) -> None:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([out_master], [], [], 0.05)
            if ready:
                try:
                    chunks.append(os.read(out_master, 65536))
                except OSError:
                    return
            elif proc.poll() is not None:
                return

    while time.monotonic() - start < wait and proc.poll() is None:
        drain(time.monotonic() + 0.05)
        if keys and not sent and time.monotonic() - start >= wait * 0.7:
            with contextlib.suppress(OSError):
                os.write(in_master, keys.encode())
            sent = True
    # A graceful key lets the app finish its last paint; SIGTERM can cut a
    # multi-kilobyte write in half and leave a corrupt escape in the stream.
    if proc.poll() is None:
        with contextlib.suppress(OSError):
            os.write(in_master, b"q")
        drain(time.monotonic() + 1.0)
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
    drain(time.monotonic() + 0.3)
    os.close(in_master)
    os.close(out_master)
    return b"".join(chunks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="*", help="command to snapshot (after --)")
    parser.add_argument("--from-raw", help="replay a previously captured stream")
    parser.add_argument("--cols", type=int, default=80)
    parser.add_argument("--rows", type=int, default=24)
    parser.add_argument("--wait", type=float, default=2.0, help="seconds to record")
    parser.add_argument("--keys", default="", help="keys to send near the end (e.g. '3')")
    parser.add_argument("--cwd", default=None, help="working directory for the command")
    parser.add_argument("--out", default="tui_snapshot.png")
    parser.add_argument("--raw", default=None, help="also save the raw ANSI stream")
    parser.add_argument("--cell-w", type=int, default=9)
    parser.add_argument("--cell-h", type=int, default=18)
    args = parser.parse_args(argv)

    if args.from_raw:
        with open(args.from_raw, "rb") as handle:
            raw = handle.read()
    else:
        if not args.command:
            parser.error("give a command after --, or use --from-raw")
        raw = capture(args.command, args.cols, args.rows, args.wait, args.keys, args.cwd)
        if not raw:
            # A nested `uv run` can lose a race on uv's cache lock and exit
            # before writing a byte. Give it a moment and try once more.
            time.sleep(0.5)
            raw = capture(args.command, args.cols, args.rows, args.wait, args.keys, args.cwd)
    if args.raw:
        with open(args.raw, "wb") as handle:
            handle.write(raw)

    screen = Screen(args.rows, args.cols)
    screen.feed(raw.decode("utf-8", "replace"))
    raster(screen, args.cell_w, args.cell_h).save(args.out)
    print(f"saved {args.out} ({args.cols}x{args.rows} cells, {len(raw)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
