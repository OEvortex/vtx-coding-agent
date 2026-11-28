"""Rendering compat: NO_COLOR/TERM=dumb fallback, reduced motion, resize."""

import asyncio
from typing import cast

from vx.app import VxApp, _fit_size
from vx.tui import ansi, theme
from vx.tui.terminal import Terminal


def _clean_env(monkeypatch):
    for var in (
        "NO_COLOR",
        "TERM",
        "VX_THEME",
        "FX_THEME",
        "COLORFGBG",
        "VX_REDUCED_MOTION",
        "FX_REDUCED_MOTION",
    ):
        monkeypatch.delenv(var, raising=False)


class TestColorFallback:
    def test_no_color_empties_every_style_slot(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("NO_COLOR", "1")
        theme.apply_theme(theme.FX_DARK)
        try:
            t = theme.current()
            assert t.divider_style == ""
            assert t.tag_style == ""
            assert t.syntax.keyword_style == ""
            assert t.inline_code_open == ""
            # Identity fields survive: the pick is still fx-dark, just plain.
            assert t.name == "fx-dark"
        finally:
            theme.apply_theme(theme.FX_DARK)

    def test_term_dumb_also_disables_color(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("TERM", "dumb")
        theme.apply_theme(theme.FX_LIGHT)
        try:
            assert theme.current().green_style == ""
            assert theme.current().syntax.string_style == ""
        finally:
            theme.apply_theme(theme.FX_DARK)

    def test_markup_tags_are_consumed_not_printed(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("NO_COLOR", "")
        theme.apply_theme(theme.FX_DARK)
        try:
            out = ansi.markup_to_ansi("[dim]quiet[/dim] text")
            assert "[dim]" not in out
            assert "quiet" in out
            assert "\x1b[" in out  # colors on: dim still emits SGR
        finally:
            theme.apply_theme(theme.FX_DARK)

    def test_colors_enabled_reports_the_env(self, monkeypatch):
        _clean_env(monkeypatch)
        assert theme.colors_enabled()
        monkeypatch.setenv("NO_COLOR", "")
        assert not theme.colors_enabled()
        monkeypatch.delenv("NO_COLOR")
        monkeypatch.setenv("TERM", "DUMB")
        assert not theme.colors_enabled()


class TestReducedMotion:
    def test_env_values(self, monkeypatch):
        _clean_env(monkeypatch)
        assert not theme.motion_reduced()
        monkeypatch.setenv("VX_REDUCED_MOTION", "1")
        assert theme.motion_reduced()
        monkeypatch.setenv("VX_REDUCED_MOTION", "0")
        monkeypatch.setenv("FX_REDUCED_MOTION", "true")
        # The first var set wins, and an explicit "0" is an opt-out, not a
        # request to fall through to the second name.
        assert not theme.motion_reduced()
        monkeypatch.delenv("VX_REDUCED_MOTION")
        assert theme.motion_reduced()

    def test_activity_note_holds_the_spinner_still(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv("VX_REDUCED_MOTION", "1")
        from vx.tui import footer

        still = footer.activity_note("working", 1000.0, 1000.0)
        assert still.startswith(footer.SPINNER[0])
        # The clock no longer turns the frame: every call shows frame 0.
        assert footer.activity_note("working", 1000.0, 1005.0).startswith(footer.SPINNER[0])


class TestResize:
    def test_fit_size_clamps_to_one_cell(self):
        assert _fit_size((0, 0)) == (1, 1)
        assert _fit_size((-3, 40)) == (1, 40)
        assert _fit_size((24, 80)) == (24, 80)

    def test_same_size_does_not_reset_the_painter(self):
        class Stub:
            _fd = -1

            def __init__(self):
                self.cols, self.rows = 100, 26

            def size(self):
                return self.rows, self.cols

            def write(self, data):
                pass

            def enter(self):
                pass

            def exit(self):
                pass

        term = Stub()
        app = VxApp(cwd="/tmp", terminal=cast(Terminal, term))
        app._ready = asyncio.Event()
        app._ready.set()
        resets = 0
        real_reset = app.painter.reset

        def counting():
            nonlocal resets
            resets += 1
            real_reset()

        app.painter.reset = counting
        app._draw(force=True)
        first = resets
        app._draw(force=True)  # same size: painter state is still valid
        assert resets == first
        term.cols, term.rows = 90, 20
        app._on_resize()
        assert resets == first + 1  # a real resize repaints from scratch
        assert app._last_size == (20, 90)
