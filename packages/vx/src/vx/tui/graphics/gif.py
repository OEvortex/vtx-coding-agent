"""Animated GIF decoding with nothing but zlib-free stdlib code.

GIF frames are LZW-compressed indices into a palette, plus a Graphic Control
Extension that carries the frame delay, transparent index and disposal method.
This module turns a whole file into full-canvas RGBA frames with their delays
in milliseconds, so the pet player can use the same frame clock as a sheet.

Only the pieces needed for animation are implemented: global and local colour
tables, interlaced images, transparency, and disposal methods 0-3. Extensions
that do not affect pixels (comments, application data) are skipped.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from vx.tui.graphics.png import RGBA

#: (RGBA buffer, delay in milliseconds).
type Frame = tuple[RGBA, int]


class GIFError(ValueError):
    pass


@dataclass(frozen=True)
class Gif:
    width: int
    height: int
    frames: tuple[Frame, ...]
    loop: int

    @property
    def delays(self) -> tuple[int, ...]:
        return tuple(delay for _pixels, delay in self.frames)


def _sub_blocks(data: bytes, pos: int) -> tuple[bytes, int]:
    """Concatenate GIF sub-blocks, returning the bytes and the next offset."""
    out = bytearray()
    while pos < len(data):
        size = data[pos]
        pos += 1
        if size == 0:
            return bytes(out), pos
        out += data[pos : pos + size]
        pos += size
    raise GIFError("unterminated sub-block chain")


def _table(data: bytes, size: int) -> list[tuple[int, int, int]]:
    need = size * 3
    if len(data) < need:
        raise GIFError("short colour table")
    return [(data[i], data[i + 1], data[i + 2]) for i in range(0, need, 3)]


def _interlaced_order(height: int) -> list[int]:
    rows: list[int] = []
    for start, step in ((0, 8), (4, 8), (2, 4), (1, 2)):
        rows.extend(range(start, height, step))
    return rows


def _lzw(data: bytes, minimum: int, width: int, height: int) -> bytes:
    """Expand an LZW image stream into one byte per pixel (palette indices)."""
    clear_code = 1 << minimum
    end_code = clear_code + 1
    dictionary = [bytes((i,)) for i in range(clear_code)] + [b"", b""]
    code_size = minimum + 1
    bit = 0
    previous: bytes | None = None
    out = bytearray()

    def read_code() -> int | None:
        nonlocal bit
        value = 0
        for offset in range(code_size):
            index = bit // 8
            if index >= len(data):
                return None
            value |= (data[index] >> (bit % 8) & 1) << offset
            bit += 1
        return value

    while True:
        code = read_code()
        if code is None:
            break
        if code == clear_code:
            dictionary = [bytes((i,)) for i in range(clear_code)] + [b"", b""]
            code_size = minimum + 1
            previous = None
            continue
        if code == end_code:
            break
        if code < len(dictionary) and dictionary[code]:
            entry = dictionary[code]
        elif previous is not None and code == len(dictionary):
            entry = previous + previous[:1]
        else:
            raise GIFError(f"bad LZW code {code} (next {len(dictionary)})")
        out += entry
        if previous is not None and len(dictionary) < 4096:
            dictionary.append(previous + entry[:1])
            if len(dictionary) == (1 << code_size) and code_size < 12:
                code_size += 1
        previous = entry
        if len(out) >= width * height:
            break
    if len(out) < width * height:
        raise GIFError(f"short LZW stream: {len(out)} of {width * height} pixels")
    return bytes(out[: width * height])


def decode(data: bytes) -> Gif:
    """GIF bytes -> a :class:`Gif` with one RGBA buffer and delay per frame."""
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise GIFError("not a GIF")
    width, height, packed, _background, _aspect = struct.unpack("<HHBBB", data[6:13])
    pos = 13
    global_table: list[tuple[int, int, int]] = []
    if packed & 0x80:
        global_table = _table(data[pos:], 2 << (packed & 7))
        pos += 3 * len(global_table)

    disposal = 0
    pending_disposal = 0
    restore_canvas: bytes | None = None
    delay = 100
    transparent: int | None = None
    loop = 0
    canvas = bytearray(width * height * 4)
    frames: list[Frame] = []

    while pos < len(data):
        marker = data[pos]
        if marker == 0x3B:  # trailer
            break
        if marker == 0x21:  # extension
            label = data[pos + 1]
            pos += 2
            if label == 0xF9:  # graphic control
                size = data[pos]
                block = data[pos + 1 : pos + 1 + size]
                pos += 1 + size + 1
                if size < 4:
                    raise GIFError("short graphic control extension")
                flags = block[0]
                disposal = (flags >> 2) & 7
                delay = struct.unpack("<H", block[1:3])[0] * 10 or 100
                transparent = block[3] if flags & 1 else None
            elif label == 0xFF:  # application (loop count lives here)
                name_size = data[pos]
                name = data[pos + 1 : pos + 1 + name_size]
                pos += 1 + name_size
                payload, pos = _sub_blocks(data, pos)
                if name[:11] == b"NETSCAPE2.0" and payload[:1] == b"\x01":
                    loop = struct.unpack("<H", payload[1:3])[0]
            else:
                _body, pos = _sub_blocks(data, pos)
            continue
        if marker != 0x2C:  # image descriptor
            raise GIFError(f"unknown GIF block 0x{marker:02x}")
        left, top, iw, ih, image_flags = struct.unpack("<HHHHB", data[pos + 1 : pos + 10])
        pos += 10
        table = global_table
        if image_flags & 0x80:
            table = _table(data[pos:], 2 << (image_flags & 7))
            pos += 3 * len(table)
        minimum = data[pos]
        pos += 1
        packed_lzw, pos = _sub_blocks(data, pos)
        indices = _lzw(packed_lzw, minimum, iw, ih)
        rows = _interlaced_order(ih) if image_flags & 0x40 else list(range(ih))

        # The previous frame's disposal applies before this one is drawn.
        if pending_disposal == 2:
            for y in range(top, min(top + ih, height)):
                start = (y * width + left) * 4
                canvas[start : start + min(iw, width - left) * 4] = bytes(
                    min(iw, width - left) * 4
                )
        elif pending_disposal == 3 and restore_canvas is not None:
            canvas[:] = restore_canvas
        before = bytes(canvas) if disposal == 3 else None
        for sy, row in enumerate(rows):
            if top + row >= height:
                continue
            dst = ((top + row) * width + left) * 4
            for x in range(min(iw, width - left)):
                index = indices[sy * iw + x]
                source = dst + x * 4
                if transparent is not None and index == transparent:
                    canvas[source + 3] = 0
                    continue
                r, g, b = table[index] if index < len(table) else (0, 0, 0)
                canvas[source] = r
                canvas[source + 1] = g
                canvas[source + 2] = b
                canvas[source + 3] = 255
        frames.append(((width, height, bytes(canvas)), delay))
        pending_disposal = disposal
        restore_canvas = before
        disposal = 0
        transparent = None
        delay = 100

    if not frames:
        raise GIFError("GIF has no frames")
    return Gif(width, height, tuple(frames), loop)


def load(path: str | Path) -> Gif:
    with open(path, "rb") as handle:
        return decode(handle.read())
