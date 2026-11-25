"""Sprite-sheet playback for the terminal pet."""

from __future__ import annotations

import time

import pytest
from vx import pet as petmod
from vx.pet import MOTIONS, ORDER, Pet, Spritesheet, find_sheet
from vx.tui.graphics import png


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


def test_advance_uses_the_clock_when_none(tmp_path, small_sheet):
    path = tmp_path / "sheet.png"
    path.write_bytes(small_sheet)
    pet = Pet(Spritesheet(path))
    pet._deadline = time.monotonic() - 1
    assert pet.advance()
