"""PNG in and out with nothing but zlib.

The TUI is stdlib-only by design (``ansi``/``canvas`` import no third-party
packages), so terminal graphics cannot lean on Pillow. A PNG is a zlib stream
of filtered scanlines, and both directions are short enough to keep here:

* ``decode`` handles 8-bit truecolour and truecolour+alpha, non-interlaced.
  That covers sprite sheets, logos and screenshots.
* ``encode`` writes the same shape back out, which is what the kitty and
  iTerm2 protocols want to send (both carry a whole PNG, not raw pixels).

Decoding a 1536x2288 sheet costs a second or two in pure Python, so the result
is cached in a sidecar ``.rgba`` file keyed by the source's size and mtime.
"""

from __future__ import annotations

import os
import struct
import zlib

#: (width, height, RGBA bytes) — rows of 4-byte pixels, row-major.
type RGBA = tuple[int, int, bytes]

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CACHE_VERSION = b"VXRGBA1\n"


class PNGError(ValueError):
    pass


def _chunks(data: bytes):
    i = len(_SIGNATURE)
    while i + 8 <= len(data):
        (length,) = struct.unpack(">I", data[i : i + 4])
        kind = data[i + 4 : i + 8]
        yield kind, data[i + 8 : i + 8 + length]
        i += 12 + length


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw: bytes, stride: int, rows: int, bpp: int) -> bytearray:
    """Undo the five PNG scanline filters into one contiguous buffer."""
    out = bytearray(stride * rows)
    prev = memoryview(out)[0:stride]
    pos = 0
    for y in range(rows):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos : pos + stride])
        pos += stride
        if ftype == 0:
            pass
        elif ftype == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        else:
            raise PNGError(f"bad filter type {ftype} on row {y}")
        start = y * stride
        out[start : start + stride] = line
        prev = memoryview(out)[start : start + stride]
    return out


def _to_rgba(width: int, height: int, raw: bytes | bytearray, channels: int) -> bytes:
    if channels == 4:
        return bytes(raw[: width * height * 4])
    rgb = bytearray(len(raw) // 3 * 4)
    rgb[3::4] = b"\xff" * (len(raw) // 3)
    rgb[0::4] = raw[0::3]
    rgb[1::4] = raw[1::3]
    rgb[2::4] = raw[2::3]
    return bytes(rgb)


def decode(data: bytes) -> RGBA:
    """PNG bytes -> ``(width, height, rgba)``."""
    if not data.startswith(_SIGNATURE):
        raise PNGError("not a PNG")
    idat: list[bytes] = []
    width = height = channels = 0
    for kind, body in _chunks(data):
        if kind == b"IHDR":
            width, height, depth, ctype, _comp, _filt, interlace = struct.unpack(
                ">IIBBBBB", body[:13]
            )
            if depth != 8 or ctype not in (2, 6):
                raise PNGError(f"unsupported depth={depth} colour type={ctype}")
            if interlace:
                raise PNGError("interlaced PNGs are not supported")
            channels = 3 if ctype == 2 else 4
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if not width or not height:
        raise PNGError("missing IHDR")
    stride = width * channels
    raw = zlib.decompress(b"".join(idat))
    pixels = _unfilter(raw, stride, height, channels)
    return width, height, _to_rgba(width, height, pixels, channels)


def encode(width: int, height: int, rgba: bytes) -> bytes:
    """``(width, height, rgba)`` -> PNG bytes (8-bit RGBA, filter type 0).

    Always re-encodes from scratch: there is no need to preserve the source's
    compression, only to hand a valid PNG to a terminal.
    """
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride : (y + 1) * stride]

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        _SIGNATURE
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


# -- file helpers ------------------------------------------------------------


def _cache_path(path: str) -> str:
    return path + ".rgba"


def _cache_key(path: str) -> bytes:
    st = os.stat(path)
    return f"{st.st_size}:{int(st.st_mtime)}".encode()


def load(path: str, cache: bool = True) -> RGBA:
    """Decode a PNG file, reusing a sidecar cache when it is still valid.

    The cache is best effort: an unwritable directory just means the sheet is
    decoded again next time, never an error.
    """
    key = _cache_key(path) if cache else b""
    cpath = _cache_path(path)
    if cache:
        cached = _read_cache(cpath, key)
        if cached is not None:
            return cached
    with open(path, "rb") as handle:
        width, height, rgba = decode(handle.read())
    if cache:
        _write_cache(cpath, key, width, height, rgba)
    return width, height, rgba


def _read_cache(cpath: str, key: bytes) -> RGBA | None:
    try:
        with open(cpath, "rb") as handle:
            blob = handle.read()
        head = len(_CACHE_VERSION)
        (key_len,) = struct.unpack(">H", blob[head : head + 2])
        offset = head + 2 + key_len
        if blob[head + 2 : offset] != key:
            return None
        width, height = struct.unpack(">II", blob[offset : offset + 8])
        offset += 8
        size = width * height * 4
        if len(blob) - offset != size:
            return None
        return width, height, blob[offset : offset + size]
    except (OSError, struct.error):
        return None


def _write_cache(cpath: str, key: bytes, width: int, height: int, rgba: bytes) -> None:
    try:
        tmp = f"{cpath}.{os.getpid()}.tmp"
        with open(tmp, "wb") as handle:
            handle.write(
                _CACHE_VERSION
                + struct.pack(">H", len(key))
                + key
                + struct.pack(">II", width, height)
                + rgba
            )
        os.replace(tmp, cpath)
    except OSError:
        pass


def save(path: str, width: int, height: int, rgba: bytes) -> None:
    with open(path, "wb") as handle:
        handle.write(encode(width, height, rgba))


# -- geometry ----------------------------------------------------------------


def crop(src: RGBA, x: int, y: int, w: int, h: int) -> RGBA:
    """Copy a rectangle out of a sheet; out-of-range pixels read as clear."""
    sw, sh, pixels = src
    out = bytearray(w * h * 4)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(sw, x + w), min(sh, y + h)
    row_bytes = (x1 - x0) * 4
    for sy in range(y0, y1):
        src_start = (sy * sw + x0) * 4
        dst_start = ((sy - y) * w + (x0 - x)) * 4
        out[dst_start : dst_start + row_bytes] = pixels[src_start : src_start + row_bytes]
    return w, h, bytes(out)


#: A dark cluster covering at least this share of a cell wins it over the
#: majority colour. Line art is 1-2 source pixels wide, so plain majority
#: sampling of a 4:1 reduction deletes every eye, mouth and outline; letting
#: ink claim its cells keeps the drawing readable instead of showing a blob.
INK_SHARE = 0.16
#: ...but only when the cluster is this much darker than the majority, so body
#: shading does not get promoted to outline.
INK_CONTRAST = 90.0
#: Pixels below this luminance are dark line art (the hachiware outline is a
#: very dark maroon, not pure black). Antialiased edge pixels land here too, so
#: they vote with the outline instead of washing the contour into the body.
INK_LUM = 200.0


def _lum(r: float, g: float, b: float) -> float:
    return 0.299 * r + 0.587 * g + 0.114 * b


def _category(r: float, g: float, b: float) -> int:
    """Classify a flat-colour pixel before the vote: 1 ink, 2 blush, 3 blue, 0 other.

    Blue ears and pink cheeks are distinct from the dark outline even though
    all three are darker than the white body. Without the split they get swept
    into the ink heuristic and come out as brown smears.
    """
    if r > 190 and r > g + 25 and r > b + 10:
        return 2
    if b > 120 and b > r + 15:
        return 3
    return 1 if _lum(r, g, b) < INK_LUM else 0


def dominant(
    src: RGBA, w: int, h: int, *, ink_share: float = INK_SHARE, ink_contrast: float = INK_CONTRAST
) -> RGBA:
    """Downscale flat-colour art by vote, keeping outlines, accents and alpha.

    Averaging an 8:1 block turns a crisp outline into a grey wash and bleeds
    body colour into the background: that is the fog. Every source pixel is
    first classified as ink, blush, blue or plain, then bucketed by colour so
    antialiased variants vote together. The winning bucket paints the output
    pixel, and alpha stays binary by majority coverage.

    A thin outline still loses a straight vote, so dark buckets that cover
    ``ink_share`` of the cell and sit ``ink_contrast`` below the majority take
    the cell instead. Classifying first is what keeps a blue ear or a pink
    cheek from being mistaken for that outline.
    """
    sw, sh, pixels = src
    if w == sw and h == sh:
        return src
    out = bytearray(w * h * 4)
    for dy in range(h):
        sy0 = dy * sh // h
        sy1 = max(sy0 + 1, (dy + 1) * sh // h)
        for dx in range(w):
            sx0 = dx * sw // w
            sx1 = max(sx0 + 1, (dx + 1) * sw // w)
            buckets: dict[int, list[int]] = {}
            total = 0
            for sy in range(sy0, sy1):
                base = (sy * sw + sx0) * 4
                for sx in range(sx0, sx1):
                    o = base + (sx - sx0) * 4
                    total += 1
                    if pixels[o + 3] < 128:
                        continue
                    r, g, b = pixels[o], pixels[o + 1], pixels[o + 2]
                    key = (r >> 4) << 8 | (g >> 4) << 4 | (b >> 4)
                    slot = buckets.get(key)
                    if slot is None:
                        buckets[key] = [1, r, g, b, _category(r, g, b)]
                    else:
                        slot[0] += 1
                        slot[1] += r
                        slot[2] += g
                        slot[3] += b
            o = (dy * w + dx) * 4
            if not buckets:
                continue
            opaque = sum(slot[0] for slot in buckets.values())
            count, r, g, b, _ = max(buckets.values(), key=lambda slot: slot[0])
            if ink_share > 0 and len(buckets) > 1:
                # Sum whole ink buckets: an anti-aliased outline is spread over
                # a dozen 1-pixel buckets, so picking only the darkest one
                # would always fall under the share. Blue and blush never join.
                mode_lum = _lum(r / count, g / count, b / count)
                ink_n = ink_r = ink_g = ink_b = 0
                for slot in buckets.values():
                    if slot[4] != 1:
                        continue
                    if (
                        mode_lum - _lum(slot[1] / slot[0], slot[2] / slot[0], slot[3] / slot[0])
                        >= ink_contrast
                    ):
                        ink_n += slot[0]
                        ink_r += slot[1]
                        ink_g += slot[2]
                        ink_b += slot[3]
                if ink_n >= opaque * ink_share:
                    count, r, g, b = ink_n, ink_r, ink_g, ink_b
            out[o] = r // count
            out[o + 1] = g // count
            out[o + 2] = b // count
            out[o + 3] = 255 if count * 2 >= total else 0
    return w, h, bytes(out)


def scale(src: RGBA, w: int, h: int, *, gamma: float = 1.0) -> RGBA:
    """Box-filtered downscale (nearest for upscale) — good enough for cells.

    Terminal cells are 1-2px wide targets, so averaging a whole source block
    is both faster and cleaner than sampling a single pixel: pixel art keeps
    its edges instead of dropping rows. ``gamma > 1`` averages in linear light
    instead of sRGB, which keeps highlights from being dragged down by the
    surrounding dark pixels in photographic frames.
    """
    sw, sh, pixels = src
    if w == sw and h == sh:
        return src
    linear = (
        [min(255, round((i / 255) ** gamma * 255)) for i in range(256)] if gamma != 1.0 else []
    )
    srgb = [round((i / 255) ** (1 / gamma) * 255) for i in range(256)] if gamma != 1.0 else []
    out = bytearray(w * h * 4)
    for dy in range(h):
        sy0 = dy * sh // h
        sy1 = max(sy0 + 1, (dy + 1) * sh // h)
        for dx in range(w):
            sx0 = dx * sw // w
            sx1 = max(sx0 + 1, (dx + 1) * sw // w)
            r = g = b = a = n = 0
            for sy in range(sy0, sy1):
                base = (sy * sw + sx0) * 4
                for sx in range(sx0, sx1):
                    o = base + (sx - sx0) * 4
                    pa = pixels[o + 3]
                    r += (linear[pixels[o]] if linear else pixels[o]) * pa
                    g += (linear[pixels[o + 1]] if linear else pixels[o + 1]) * pa
                    b += (linear[pixels[o + 2]] if linear else pixels[o + 2]) * pa
                    a += pa
                    n += 1
            o = (dy * w + dx) * 4
            if a:
                if srgb:
                    out[o] = srgb[min(255, r // a)]
                    out[o + 1] = srgb[min(255, g // a)]
                    out[o + 2] = srgb[min(255, b // a)]
                else:
                    out[o] = min(255, r // a)
                    out[o + 1] = min(255, g // a)
                    out[o + 2] = min(255, b // a)
            out[o + 3] = a // n if n else 0
    return w, h, bytes(out)
