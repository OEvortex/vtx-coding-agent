"""Terminal graphics: PNG codec, half blocks, and the pixel protocols.

The sixel and kitty checks decode the emitted sequence back into pixels, so a
broken encoder fails here instead of appearing as a smear in somebody's
terminal.
"""

from __future__ import annotations

import base64
import os
import pty
import re
import tty

import pytest
from vx.tui import graphics
from vx.tui.graphics import blocks, braille, gif, palette, png, protocols, quadrants


def tiny_gif() -> bytes:
    """Three 4x4 frames: 100/200/300 ms, loop twice, index 0 transparent."""
    return base64.b64decode(
        "R0lGODlhBAAEAIEAAP8AAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwECAAAh+QQJCgAAACwAAAAABAAEAAAICQABCBxIsCCAgAAh+QQIFAAAACwAAAAABAAEAIEA/wAAAAAAAAAAAAAICQABCBxIsCCAgAAh+QQIHgAAACwAAAAABAAEAIEAAP8AAAAAAAAAAAAICQABCBxIsCCAgAA7"
    )


def canvas(width: int = 8, height: int = 8) -> png.RGBA:
    """A tiny RGBA test image: red/white quarters plus a clear corner."""
    data = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            o = (y * width + x) * 4
            if x < width // 2 and y < height // 2:
                data[o : o + 4] = b"\xff\x00\x00\xff"
            elif x >= width // 2 and y >= height // 2:
                data[o : o + 4] = b"\x00\x00\xff\xff"
            else:
                data[o : o + 3] = b"\xff\xff\xff"
                data[o + 3] = 0 if x == width - 1 else 0xFF
    return width, height, bytes(data)


def noise(width: int = 64, height: int = 64) -> png.RGBA:
    """Incompressible pixels, so the PNG is big enough to need chunking."""
    data = bytearray(width * height * 4)
    seed = 12345
    for i in range(width * height):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        data[i * 4 : i * 4 + 4] = bytes((seed >> 16 & 0xFF, seed >> 8 & 0xFF, seed & 0xFF, 0xFF))
    return width, height, bytes(data)


# -- png ---------------------------------------------------------------------


def test_png_roundtrip_keeps_pixels():
    src = canvas()
    back = png.decode(png.encode(*src))
    assert back == src


def test_png_load_uses_cache(tmp_path):
    path = tmp_path / "sheet.png"
    png.save(str(path), *canvas())
    first = png.load(str(path))
    assert path.with_suffix(".png.rgba").exists()
    assert png.load(str(path)) == first


def test_png_rejects_interlaced(tmp_path):
    src = bytearray(png.encode(*canvas()))
    src[28] = 1  # IHDR interlace flag
    with pytest.raises(png.PNGError):
        png.decode(bytes(src))


def test_crop_and_scale():
    width, height, data = canvas(16, 16)
    w, h, part = png.crop((width, height, data), 8, 8, 8, 8)
    assert (w, h) == (8, 8)
    assert part[:4] == b"\x00\x00\xff\xff"  # bottom-right quarter is blue
    sw, sh, small = png.scale((width, height, data), 4, 4)
    assert (sw, sh) == (4, 4)
    assert len(small) == 4 * 4 * 4


# -- gif ---------------------------------------------------------------------


def test_gif_decode_keeps_frames_delays_and_loop():
    animation = gif.decode(tiny_gif())
    assert (animation.width, animation.height) == (4, 4)
    assert animation.loop == 2
    assert animation.delays == (100, 200, 300)
    assert len(animation.frames) == 3


def test_gif_decode_honours_transparency_and_palette():
    frames = gif.decode(tiny_gif()).frames
    assert frames[0][0][2][3] == 0  # transparent index on the first frame
    assert frames[1][0][2][:4] == bytes((0, 255, 0, 255))
    assert frames[2][0][2][:4] == bytes((0, 0, 255, 255))


def test_gif_rejects_other_formats():
    with pytest.raises(gif.GIFError):
        gif.decode(b"not a gif")


# -- palette -----------------------------------------------------------------


def test_dominant_keeps_flat_colours_instead_of_averaging():
    """The fog fix: a 3:1 pixel keeps its own colour, it does not blend."""
    white = bytes((255, 255, 255, 255))
    grey = bytes((200, 200, 200, 255))
    pixels = (2, 2, white * 3 + grey)
    _w, _h, data = png.dominant(pixels, 1, 1, ink_share=0.0)
    assert tuple(data[:3]) == (255, 255, 255)
    _w, _h, averaged = png.scale(pixels, 1, 1)
    assert averaged[0] < 250  # the old path blended white towards grey


def test_dominant_keeps_thin_dark_line_art():
    """A 1-pixel outline cannot outvote the body, but it must not vanish."""
    row = (
        bytes((255, 255, 255, 255)) * 3
        + bytes((40, 10, 20, 255))
        + bytes((255, 255, 255, 255)) * 32
    )
    pixels = (6, 6, row * 6)
    # a 2x2 cell sees 3 white pixels and 1 dark pixel: 25% ink
    _w, _h, data = png.dominant(pixels, 3, 3)
    dark = [i for i in range(0, len(data), 4) if data[i] < 120]
    assert dark, "the outline was outvoted into nothing"


def test_dominant_leaves_soft_shading_to_the_majority():
    """Body shading is not ink: a low-contrast neighbour keeps the majority."""
    light = bytes((250, 250, 250, 255))
    shade = bytes((210, 210, 210, 255))
    row = light * 3 + shade * 3
    pixels = (6, 6, row * 6)
    _w, _h, data = png.dominant(pixels, 1, 1, ink_share=0.2)
    assert data[0] > 230


def test_dominant_drops_majority_transparent_pixels():
    pixels = (2, 2, bytes((255, 0, 0, 255)) + bytes((0, 0, 0, 0)) * 3)
    _w, _h, data = png.dominant(pixels, 1, 1)
    assert data[3] == 0


def test_dominant_classifies_accent_colours_before_ink():
    """Blue ears and pink cheeks are accents, not line art, even when dark."""
    assert png._category(122, 176, 214) == 3  # blue ear
    assert png._category(252, 186, 198) == 2  # pink cheek
    assert png._category(41, 0, 0) == 1  # maroon outline
    assert png._category(254, 254, 254) == 0  # white body


def test_palette_avoids_theme_remappable_entries():
    # Exact matches inside the fixed cube and grey ramp, never the first 16
    # entries, which a terminal theme is free to recolour.
    assert palette.nearest(255, 0, 0) == 196
    assert palette.nearest(0, 0, 0) == 16
    assert palette.nearest(255, 255, 255) == 231
    assert palette.nearest(128, 128, 128) == 244
    assert palette.nearest(0, 0, 255) == 21
    assert palette.rgb(196) == (255, 0, 0)
    # Anti-aliased outline pixels used to snap to bright maroon; now they land
    # on a dark grey, which is what the pixel actually looks like.
    assert palette.nearest(70, 33, 42) in (235, 236)
    assert all(palette.nearest(0, 0, 0) < 255 for _ in range(1))


# -- half blocks -------------------------------------------------------------


def test_blocks_render_one_row_per_two_pixel_rows():
    rows = blocks.render(canvas(8, 8), 8, 8)
    assert len(rows) == 4
    for row in rows:
        assert graphics.blocks.ansi.width(row) == 8


def test_truecolor_support_follows_colorterm():
    assert blocks.supports_truecolor({"COLORTERM": "truecolor"})
    assert blocks.supports_truecolor({"COLORTERM": "24bit"})
    assert blocks.supports_truecolor({"TERM": "xterm-direct"})
    assert not blocks.supports_truecolor({"TERM": "xterm-256color"})


def test_blocks_keep_transparency_and_never_pad_left():
    # 4x4 with: left column red-over-clear, next column clear-over-blue.
    data = bytearray(4 * 4 * 4)
    red = b"\xff\x00\x00\xff"
    blue = b"\x00\x00\xff\xff"
    data[0:4] = red  # (0, 0)
    data[(1 * 4 + 1) * 4 : (1 * 4 + 2) * 4] = blue  # (1, 1)
    rows = blocks.render((4, 4, bytes(data)), 4, 4, mode=blocks.MODE_256)
    # Upper half alone uses ▀ with the default background, lower half alone
    # uses ▄; the two clear cells stay spaces instead of being trimmed, and a
    # fully clear bottom row is dropped rather than emitting blank cells.
    assert rows == ["\x1b[38;5;196m\u2580\x1b[38;5;21m\u2584  \x1b[0m"]
    assert graphics.blocks.ansi.width(rows[0]) == 4


def test_blocks_truecolor_sends_exact_rgb():
    # Same pixels, 24-bit SGR: no palette lookup at all.
    rows = blocks.render(canvas(2, 2), 2, 1, mode=blocks.MODE_TRUE)
    assert rows == [
        "\x1b[38;2;255;0;0m\x1b[48;2;255;255;255m\u2580\x1b[38;2;0;0;255m\x1b[49m\u2584\x1b[0m"
    ]


def test_blocks_reset_background_before_a_gap():
    """A transparent cell must not paint the last pixel's bg across the gap."""
    data = bytearray(4 * 2 * 4)
    for y in range(2):
        for x in range(2):
            data[(y * 4 + x) * 4 : (y * 4 + x) * 4 + 4] = b"\xff\xff\xff\xff"
    row = blocks.render((4, 2, bytes(data)), 4, 2, mode=blocks.MODE_TRUE)[0]
    assert row == "\x1b[38;2;255;255;255m\x1b[48;2;255;255;255m\u2580\u2580\x1b[49m  \x1b[0m"


def test_blocks_box_preserves_aspect():
    assert blocks.box(192, 208, 24, 40) == (24, 26)
    # A short box wins: 3 cell rows are 6 pixels, so the width shrinks with it.
    assert blocks.box(192, 208, 200, 3) == (6, 6)


def test_empty_image_has_no_rows():
    blank = (4, 4, bytes(4 * 4 * 4))
    assert blocks.rows(blank, 4, 4) == []


# -- quadrants ---------------------------------------------------------------


def test_quadrant_box_doubles_horizontal_resolution():
    assert quadrants.box(192, 208, 24, 40) == (48, 26)
    assert quadrants.fit(192, 208, 24, 40) == (24, 13)
    # A short box wins: six pixel rows leave only twelve subpixel columns.
    assert quadrants.box(192, 208, 200, 3) == (12, 6)


def test_quadrant_full_cell_is_one_solid_glyph():
    red = bytes((255, 0, 0, 255))
    assert quadrants.rows((2, 2, red * 4), 2, 2) == ["\x1b[38;2;255;0;0m█\x1b[0m"]


def test_quadrant_transparent_subpixels_use_the_theme_background():
    data = bytearray(2 * 2 * 4)
    data[0:4] = bytes((255, 0, 0, 255))
    row = quadrants.rows((2, 2, bytes(data)), 2, 2)[0]
    # ▘ paints only the top-left quadrant; the other three must not be painted
    # with the sprite's red, or the sprite edge grows coloured specks.
    assert "▘" in row
    assert "\x1b[49m" in row


def test_quadrant_seams_blend_into_the_majority_colour():
    # Three white quadrants and one dark pixel: the cell background should be
    # white so the font's hairline gaps do not draw a dark grid across the hair.
    white, dark = bytes((255, 255, 255, 255)), bytes((0, 0, 0, 255))
    row = quadrants.rows((2, 2, white * 3 + dark), 2, 2)[0]
    assert "▗" in row
    assert "\x1b[48;2;255;255;255m" in row


def test_quadrant_256_mode_uses_the_palette():
    red = bytes((255, 0, 0, 255))
    assert "\x1b[38;5;" in quadrants.rows((2, 2, red * 4), 2, 2, mode=blocks.MODE_256)[0]


def test_quadrant_empty_image_has_no_rows():
    assert quadrants.rows((4, 4, bytes(4 * 4 * 4)), 4, 4) == []


def test_render_dispatches_to_quadrants():
    red = bytes((255, 0, 0, 255))
    pixels = (2, 2, red * 4)
    assert graphics.render(pixels, 1, 1, backend="quadrants") == quadrants.render(pixels, 1, 1)


# -- braille -----------------------------------------------------------------


def test_braille_box_uses_two_by_four_subpixels():
    assert braille.box(192, 208, 24, 40) == (48, 52)
    assert braille.fit(192, 208, 24, 40) == (24, 13)


def test_braille_uniform_cell_uses_a_solid_block():
    red = bytes((255, 0, 0, 255))
    assert braille.rows((2, 4, red * 8), 2, 4) == ["\x1b[38;2;255;0;0m█\x1b[0m"]


def test_braille_single_dot_keeps_the_theme_background():
    data = bytearray(2 * 4 * 4)
    data[0:4] = bytes((255, 0, 0, 255))
    row = braille.rows((2, 4, bytes(data)), 2, 4)[0]
    assert "⠁" in row  # U+2801, dot 1 only
    assert "\x1b[49m" in row


def test_braille_render_dispatch_and_selection():
    red = bytes((255, 0, 0, 255))
    pixels = (2, 4, red * 8)
    assert graphics.render(pixels, 1, 1, backend="braille") == braille.render(pixels, 1, 1)
    assert graphics.Caps().cycle_backends == ("blocks", "quadrants", "braille")
    assert graphics.detect(env={"VTX_GFX": "braille"}).best() == "braille"


# -- sixel -------------------------------------------------------------------


def _decode_sixel(sequence: str) -> tuple[int, int, bytes]:
    """Minimal sixel reader, enough to verify what ``protocols.sixel`` emits."""
    body = sequence[sequence.index("q") + 1 : -2]
    raster = re.search(r'"1;1;(\d+);(\d+)', body)
    assert raster is not None
    width, height = int(raster.group(1)), int(raster.group(2))
    colours: dict[int, tuple[int, int, int]] = {}
    pixels = bytearray(width * height * 4)
    selected = 0
    x = 0
    band = 0
    i = raster.end()
    while i < len(body):
        ch = body[i]
        if ch == "#":
            match = re.match(r"#(\d+)(?:;2;(\d+);(\d+);(\d+))?", body[i:])
            assert match is not None
            selected = int(match.group(1))
            if match.group(2):
                colours[selected] = (
                    int(match.group(2)) * 255 // 100,
                    int(match.group(3)) * 255 // 100,
                    int(match.group(4)) * 255 // 100,
                )
            i += match.end()
            continue
        if ch == "$":
            x = 0
            i += 1
            continue
        if ch == "-":
            x = 0
            band += 1
            i += 1
            continue
        if ch == "!":
            match = re.match(r"!(\d+)(.)", body[i:])
            assert match is not None
            count, char = int(match.group(1)), match.group(2)
            i += match.end()
        else:
            count, char = 1, ch
            i += 1
        bits = ord(char) - 0x3F
        assert 0 <= bits < 64
        colour = colours.get(selected, (0, 0, 0))
        for _ in range(count):
            for k in range(6):
                y = band * 6 + k
                if bits >> k & 1 and y < height and x < width:
                    o = (y * width + x) * 4
                    pixels[o : o + 4] = bytes((*colour, 255))
            x += 1
    return width, height, bytes(pixels)


def test_sixel_roundtrip():
    src = canvas(8, 8)
    decoded = _decode_sixel(protocols.sixel(src))
    width, height, pixels = decoded
    assert (width, height) == (8, 8)
    source = png.scale(src, 8, 8)[2]
    # Only opaque source pixels are painted, and they land in the right place.
    for i in range(width * height):
        if source[i * 4 + 3] >= blocks.ALPHA_MIN:
            assert pixels[i * 4 + 3] == 255, f"pixel {i} missing"
    assert pixels[0:3] == bytes(palette.rgb(palette.nearest(255, 0, 0)))
    corner = (7 * width + 7) * 4
    assert pixels[corner : corner + 3] == bytes(palette.rgb(palette.nearest(0, 0, 255)))


def test_sixel_is_a_well_formed_dcs():
    sequence = protocols.sixel(canvas())
    assert sequence.startswith("\x1bP0;1;0q")
    assert sequence.endswith("\x1b\\")
    assert '"1;1;8;8' in sequence


def test_sixel_of_blank_image_is_empty():
    assert protocols.sixel((4, 4, bytes(64))) == ""


# -- kitty and iTerm2 --------------------------------------------------------


def _kitty_payload(sequence: str) -> bytes:
    chunks = []
    for part in sequence.split("\x1b_G")[1:]:
        _control, body = part.split(";", 1)
        chunks.append(body[: -len("\x1b\\")])
    return base64.b64decode("".join(chunks))


def test_kitty_roundtrip():
    src = canvas()
    sequence = protocols.kitty(src, cols=6, rows=3, image_id=7)
    assert "a=T,f=100,c=6,r=3,i=7" in sequence
    assert png.decode(_kitty_payload(sequence)) == src


def test_kitty_chunks_stay_under_the_escape_limit():
    src = noise(64, 64)
    sequence = protocols.kitty(src, cols=8, rows=4)
    pieces = sequence.split("\x1b_G")[1:]
    assert len(pieces) > 1
    assert all(len(f"\x1b_G{piece}") <= 4096 for piece in pieces)
    assert ",m=1;" in pieces[0] and ",m=0;" in pieces[-1]


def test_kitty_delete_and_iterm():
    assert protocols.kitty_delete(3) == "\x1b_Ga=d,d=i,i=3,q=2\x1b\\"
    sequence = protocols.iterm(canvas(), cols=5, rows=2)
    assert sequence.startswith("\x1b]1337;File=inline=1")
    assert "width=5;height=2" in sequence
    assert png.decode(base64.b64decode(sequence.split(":", 1)[1][:-1])) == canvas()


def test_paint_dispatch():
    src = canvas()
    assert protocols.paint("blocks", src, 4, 4) == ""
    assert protocols.paint("sixel", src, 4, 4).startswith("\x1bP")
    assert protocols.paint("kitty", src, 4, 4).startswith("\x1b_G")
    assert protocols.paint("iterm", src, 4, 4).startswith("\x1b]1337")


# -- capability detection ----------------------------------------------------


def probe_reply(cell: str, da1: str, kitty: str | None = None) -> bytes:
    reply = f"\x1b[6;{cell}t\x1b[?{da1}c".encode()
    return reply + (f"\x1b[?{kitty}u".encode() if kitty else b"")


def test_detect_from_environment():
    caps = graphics.detect(env={"TERM": "xterm-kitty"})
    assert caps.kitty and caps.source == "env"
    caps = graphics.detect(env={"TERM": "xterm-256color", "TERM_PROGRAM": "WezTerm"})
    assert caps.best() == "kitty"
    caps = graphics.detect(env={"TERM": "xterm-256color"})
    assert caps.backends == ("blocks", "quadrants", "braille") and caps.best() == "blocks"
    # VS Code needs a setting we cannot read, so it must not be guessed.
    caps = graphics.detect(env={"TERM": "xterm-256color", "TERM_PROGRAM": "vscode"})
    assert caps.best() == "blocks"


def test_cycle_backends_only_toggles_cell_backends():
    caps = graphics.detect(env={"TERM": "xterm-256color"})
    assert caps.best() == "blocks"
    assert caps.cycle_backends == ("blocks", "quadrants", "braille")


def test_detect_honours_sixel_term_hint():
    caps = graphics.detect(env={"TERM": "xterm-sixel", "TERM_PROGRAM": "vscode"})
    assert caps.sixel and caps.best() == "sixel" and caps.source == "env"


def test_detect_honours_tmux_and_override():
    caps = graphics.detect(
        env={"TERM": "screen-256color", "TMUX": "/tmp/tmux-1", "TERM_PROGRAM": "vscode"}
    )
    assert not caps.sixel and not caps.kitty
    caps = graphics.detect(env={"TERM": "xterm-256color", "VTX_GFX": "sixel"})
    assert caps.best() == "sixel" and caps.source == "forced"
    caps = graphics.detect(env={"TERM": "xterm-256color", "VTX_GFX": "quadrants"})
    assert caps.best() == "quadrants" and caps.source == "forced"


def test_detect_parses_a_probe_reply():
    caps = graphics.Caps()
    graphics._parse(probe_reply("20;10", "62;4;22", "1"), caps)
    assert caps.sixel and caps.kitty and caps.cell == (10, 20)
    caps = graphics.Caps()
    graphics._parse(probe_reply("18;9", "62;1", "0"), caps)
    assert not caps.sixel and not caps.kitty


def _pty_term(reply: bytes):
    """A stand-in terminal backed by a pty that answers with ``reply``."""
    master, slave = pty.openpty()
    tty.setraw(slave)
    os.write(master, reply)

    class FakeTerm:
        _fd = slave

    return FakeTerm(), (master, slave)


def test_detect_reads_a_live_reply():
    term, fds = _pty_term(b"\x1b[6;18;9t\x1b[?62;4;6c")
    try:
        caps = graphics.detect(term, env={"TERM": "xterm-256color"})
    finally:
        for fd in fds:
            os.close(fd)
    assert caps.probed and caps.source == "probe"
    assert caps.sixel and caps.cell == (9, 18)


def test_tmux_probe_does_not_invent_sixel():
    # tmux 3.4 answers DA1 with `4` for any client, so the reply must not be
    # trusted to add sixel that the environment did not already promise.
    term, fds = _pty_term(b"\x1b[6;32;16t\x1b[?1;2;4c")
    try:
        caps = graphics.detect(
            term, env={"TERM": "screen-256color", "TMUX": "/tmp/tmux-1000/default,1,0"}
        )
    finally:
        for fd in fds:
            os.close(fd)
    assert caps.probed and not caps.sixel
    forced = graphics.detect(
        term, env={"TERM": "screen-256color", "TMUX": "x", "VTX_GFX": "sixel"}
    )
    assert forced.best() == "sixel"


def test_probe_without_a_terminal_falls_back_to_env():
    caps = graphics.detect(env={"TERM": "xterm-256color", "TERM_PROGRAM": "iTerm.app"})
    assert caps.sixel and caps.iterm and not caps.probed


def test_pixel_box_uses_the_reported_cell_size():
    caps = graphics.Caps(cell=(9, 18))
    assert caps.pixel_box(4, 3) == (36, 54)
