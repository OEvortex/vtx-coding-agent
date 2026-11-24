"""Misc vx bits: brand constants and splash frame shape."""

from vx import brand


class TestBrand:
    def test_program_name(self):
        assert brand.PROGRAM_NAME == "vx"

    def test_version_matches_pyproject(self):
        # Keep in sync with packages/vx/pyproject.toml.
        assert brand.VERSION == "0.1.0"

    def test_italic_brand(self):
        assert brand.ITALIC_BRAND == "𝒗x"
