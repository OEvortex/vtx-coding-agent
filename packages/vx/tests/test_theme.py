"""Theme resolution: built-ins, env pins, and ctx theme conversion."""

import pytest
from vx.tui import theme


class TestSelectedTheme:
    def test_default_dark(self, monkeypatch):
        monkeypatch.delenv("VX_THEME", raising=False)
        monkeypatch.delenv("FX_THEME", raising=False)
        monkeypatch.delenv("COLORFGBG", raising=False)
        assert theme.selected_theme() is theme.FX_DARK

    def test_pin_light(self, monkeypatch):
        monkeypatch.setenv("VX_THEME", "light")
        assert theme.selected_theme() is theme.FX_LIGHT

    def test_pin_dark_overrides_fgbg(self, monkeypatch):
        monkeypatch.setenv("FX_THEME", "dark")
        monkeypatch.setenv("COLORFGBG", "0;15")
        assert theme.selected_theme() is theme.FX_DARK

    def test_colorfgbg_light_bg(self, monkeypatch):
        monkeypatch.delenv("VX_THEME", raising=False)
        monkeypatch.delenv("FX_THEME", raising=False)
        monkeypatch.setenv("COLORFGBG", "0;15")
        assert theme.selected_theme() is theme.FX_LIGHT

    def test_colorfgbg_dark_bg(self, monkeypatch):
        monkeypatch.delenv("VX_THEME", raising=False)
        monkeypatch.delenv("FX_THEME", raising=False)
        monkeypatch.setenv("COLORFGBG", "0;0")
        assert theme.selected_theme() is theme.FX_DARK


class TestApplyNamed:
    def test_named_aliases(self):
        assert theme.apply_named("dark").name == "fx-dark"
        assert theme.apply_named("light").name == "fx-light"
        assert theme.apply_named("").name == "fx-dark"

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            theme.apply_named("no-such-theme-xyz")

    def test_apply_theme_sets_current(self):
        theme.apply_theme(theme.FX_LIGHT)
        try:
            assert theme.current() is theme.FX_LIGHT
        finally:
            theme.apply_theme(theme.FX_DARK)
