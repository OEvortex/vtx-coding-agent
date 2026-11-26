"""Sprite-sheet playback for the terminal pet."""

from __future__ import annotations

import time

import pytest
from vx import pet as petmod
from vx.pet import (
    MOTIONS,
    ORDER,
    GenericPet,
    GifPet,
    Pet,
    Spritesheet,
    Still,
    find_sheet,
    key_flat_background,
    looks_like_sheet,
)
from vx.tui.graphics import png
from vx.tui.graphics.gif import Gif


def sheet_png(cols: int = 8, rows: int = 11) -> bytes:
    """A miniature sheet: one solid colour per cell so crops are checkable."""
    cw, ch = petmod.CELL_W, petmod.CELL_H
    width, height = cols * cw, rows * ch
    data = bytearray(width * height * 4)
    for row in range(rows):
        for col in range(cols):
            value = (col * 16 + row) % 256
            for y in range(ch):
                for x in range(cw):
                    o = ((row * ch + y) * width + col * cw + x) * 4
                    data[o : o + 4] = bytes((value, 255 - value, 32, 255))
    return png.encode(width, height, bytes(data))


@pytest.fixture
def small_sheet(monkeypatch):
    """Shrink the cell size so a whole sheet stays tiny in a test."""
    monkeypatch.setattr(petmod, "CELL_W", 4)
    monkeypatch.setattr(petmod, "CELL_H", 5)
    return sheet_png()


def test_motion_table_matches_the_published_spec():
    # Frame counts come from the released GIFs, which is also what the sheet
    # holds: the README's table claims 8 frames for `failed`, but both the GIF
    # and the sheet end that row after 5.
    frames = {name: motion.frames for name, motion in MOTIONS.items()}
    assert frames == {
        "idle": 6,
        "running-right": 8,
        "running-left": 8,
        "waving": 4,
        "jumping": 5,
        "failed": 5,
        "waiting": 6,
        "running": 6,
        "review": 6,
        "look": 16,
    }
    assert [motion.row for motion in MOTIONS.values()] == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert tuple(MOTIONS) == ORDER


def test_look_spans_two_sheet_rows():
    look = MOTIONS["look"]
    assert look.cell(0) == (0, 9)
    assert look.cell(7) == (7, 9)
    assert look.cell(8) == (0, 10)
    assert look.cell(15) == (7, 10)
    assert MOTIONS["idle"].cell(3) == (3, 0)


def test_find_sheet_prefers_the_environment(tmp_path, monkeypatch):
    target = tmp_path / "pets" / "hachiware" / "spritesheet.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    monkeypatch.setenv("VTX_PET_SHEET", str(target))
    assert find_sheet() == target
    monkeypatch.delenv("VTX_PET_SHEET")
    monkeypatch.setenv("VTX_PETS_DIR", str(tmp_path / "pets"))
    assert find_sheet() == target
    assert find_sheet("nope") is None


def test_sheet_reports_its_grid(tmp_path, small_sheet):
    path = tmp_path / "sheet.png"
    path.write_bytes(small_sheet)
    sheet = Spritesheet(path)
    assert (sheet.columns, sheet.rows) == (8, 11)


def test_cell_crops_the_right_offsets(tmp_path, small_sheet):
    path = tmp_path / "sheet.png"
    path.write_bytes(small_sheet)
    sheet = Spritesheet(path)
    _w, _h, first = sheet.cell(0, 0)
    assert first[:4] == bytes((0, 255, 32, 255))
    _w, _h, other = sheet.cell(2, 1)
    value = (2 * 16 + 1) % 256
    assert other[:4] == bytes((value, 255 - value, 32, 255))


def test_play_switches_motion_and_resets_frames(tmp_path, small_sheet):
    path = tmp_path / "sheet.png"
    path.write_bytes(small_sheet)
    pet = Pet(Spritesheet(path))
    pet.index = 4
    pet.play("waving")
    assert pet.motion.name == "waving"
    assert pet.index == 0
    pet.play("waving")
    assert pet.index == 0
    pet.play("no-such-motion")
    assert pet.motion.name == "waving"


def test_advance_follows_the_recorded_delays(tmp_path, small_sheet):
    path = tmp_path / "sheet.png"
    path.write_bytes(small_sheet)
    pet = Pet(Spritesheet(path))
    start = pet.started
    assert not pet.advance(start)
    assert pet.index == 0
    assert pet.advance(start + 0.28)  # idle holds frame 0 for 280 ms
    assert pet.index == 1
    # A long stall catches up without skipping a whole loop.
    pet = Pet(Spritesheet(path))
    pet.advance(pet.started + 5.0)
    assert 0 < pet.index < pet.motion.frames


def test_frames_are_cached_after_the_first_draw(tmp_path, small_sheet):
    path = tmp_path / "sheet.png"
    path.write_bytes(small_sheet)
    slides = Pet(Spritesheet(path))
    first = slides.rows(6, 6)
    assert slides.rows(6, 6) is first
    assert len(first) <= 6
    assert all(len(row) < 200 for row in first)
    assert slides.scaled(3, 4) == slides.scaled(3, 4)


def test_rows_are_clipped_to_the_requested_box(tmp_path, small_sheet):
    path = tmp_path / "sheet.png"
    path.write_bytes(small_sheet)
    pet = Pet(Spritesheet(path))
    rows = pet.rows(5, 4)
    assert len(rows) <= 4
    for row in rows:
        assert petmod.blocks.ansi.width(row) == 5


def test_gif_pet_plays_recorded_delays_and_caches_rows():
    animation = Gif(
        2,
        2,
        (((2, 2, bytes((255, 0, 0, 255)) * 4), 100), ((2, 2, bytes((0, 0, 255, 255)) * 4), 200)),
        0,
    )
    player = GifPet(animation, smooth=False)
    assert not player.advance(player.started + 0.05)
    assert player.advance(player.started + 0.1)
    assert player.index == 1
    assert player.advance(player.started + 0.3)
    assert player.index == 0
    first = player.rows(4, 4)
    assert player.rows(4, 4) is first
    assert first == petmod.blocks.render(player.pixels(), 4, 4)


def test_advance_uses_the_clock_when_none(tmp_path, small_sheet):
    path = tmp_path / "sheet.png"
    path.write_bytes(small_sheet)
    pet = Pet(Spritesheet(path))
    pet._deadline = time.monotonic() - 1
    assert pet.advance()


def test_flood_key_keeps_dark_details_that_global_key_would_erase():
    w = h = 5
    data = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            o = (y * w + x) * 4
            border = x in (0, w - 1) or y in (0, h - 1)
            ring = x in (1, w - 2) or y in (1, h - 2)
            colour = (1, 1, 1) if border else ((255, 0, 0) if ring else (5, 5, 5))
            data[o : o + 4] = bytes((*colour, 255))
    image = (w, h, bytes(data))
    flood = key_flat_background(image, mode="flood")
    global_key = key_flat_background(image, mode="global")
    centre = (2 * w + 2) * 4 + 3
    assert flood[2][centre] == 255  # dark detail survives
    assert global_key[2][centre] == 0  # old behaviour erased it
    assert flood[2][3] == 0  # border background is cleared


def test_spritesheet_accepts_a_decoded_buffer(small_sheet):
    image = (4, 5, bytes(4 * 5 * 4))
    sheet = Spritesheet(image)
    assert (sheet.width, sheet.height) == (4, 5)
    assert sheet.cell(0, 0) == image


def test_looks_like_sheet_requires_at_least_two_cells():
    cw, ch = petmod.CELL_W, petmod.CELL_H
    assert looks_like_sheet((cw * 8, ch * 11, b""))
    assert not looks_like_sheet((cw, ch, b""))  # a single frame is a still
    assert not looks_like_sheet((100, 100, b""))


def test_sheet_accepts_a_custom_cell_size_and_row_major_index():
    data = bytearray(4 * 4 * 4)
    for i in range(16):
        data[i * 4 : i * 4 + 4] = bytes((i * 16, 0, 0, 255))
    sheet = Spritesheet((4, 4, bytes(data)), 2, 2)
    assert (sheet.columns, sheet.rows) == (2, 2)
    assert sheet.at(0) == sheet.cell(0, 0)
    assert sheet.at(3) == sheet.cell(1, 1)
    assert sheet.at(4) == sheet.cell(0, 0)  # wraps


def test_generic_pet_cycles_every_cell():
    data = bytes((255, 0, 0, 255)) * 16
    sheet = Spritesheet((4, 4, data), 2, 2)
    player = GenericPet(sheet, delay=0.01)
    assert player.index == 0
    assert player.advance(player._deadline + 0.01)
    assert player.index == 1
    assert "1,0" in player.motion.label
    for _ in range(3):
        player.advance(player._deadline + 0.01)
    assert player.index == 2  # ping-pong, not a hard cut back to cell 0
    assert player.rows(4, 4)
    player.play("idle")  # no named motions
    assert player.index == 2


def test_still_player_shows_one_image():
    image = (4, 4, bytes((255, 0, 0, 255)) * 16)
    player = Still(image, "my cat")
    assert player.motion.label == "my cat"
    assert player.advance() is False
    player.play("running-right")  # no-op
    assert player.pixels() is image
    assert player.scaled(2, 2)[:2] == (2, 2)
    rows = player.rows(4, 4)
    assert rows and rows[0]
    assert player.rows(4, 4) is rows
