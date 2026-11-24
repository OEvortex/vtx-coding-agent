"""Clipboard and @-mention image attachments."""

import base64
import sys

import pytest
from vx import attachments
from vx.attachments import Attachment, describe, image_paths_in


def _png_bytes() -> bytes:
    # Smallest valid-ish payload; content doesn't need to decode as an image here.
    return b"\x89PNG\r\n\x1a\n" + b"0" * 100


class TestAttachment:
    def test_label_prefers_name(self):
        a = Attachment(data="aGk=", mime_type="image/png", name="shot.png")
        assert a.label == "shot.png"

    def test_label_falls_back_to_mime(self):
        a = Attachment(data="aGk=", mime_type="image/png")
        assert a.label == "image/png"

    def test_size_label_units(self):
        small = Attachment(data=base64.b64encode(b"x" * 100).decode(), mime_type="image/png")
        assert small.size_label.endswith("B")
        big = Attachment(
            data=base64.b64encode(b"x" * (2 * 1024 * 1024)).decode(), mime_type="image/png"
        )
        assert big.size_label.endswith("MB")

    def test_content(self):
        a = Attachment(data="aGk=", mime_type="image/png")
        c = a.content()
        assert c.data == "aGk=" and c.mime_type == "image/png"


class TestImagePathsIn:
    def test_finds_existing_image(self, tmp_path):
        (tmp_path / "shot.png").write_bytes(_png_bytes())
        assert image_paths_in("look at @shot.png please", str(tmp_path)) == ["shot.png"]

    def test_ignores_non_image_suffix(self, tmp_path):
        (tmp_path / "notes.txt").write_text("x")
        assert image_paths_in("@notes.txt", str(tmp_path)) == []

    def test_ignores_missing_file(self, tmp_path):
        assert image_paths_in("@ghost.png", str(tmp_path)) == []

    def test_ignores_plain_at_word(self, tmp_path):
        (tmp_path / "a.png").write_bytes(_png_bytes())
        # "x@a.png" does not start the token with @... token is "x@a.png", no match.
        assert image_paths_in("x@a.png", str(tmp_path)) == []


class TestDescribe:
    def test_empty(self):
        assert describe([]) == ""

    def test_one(self):
        a = Attachment(
            data=base64.b64encode(b"x" * 2048).decode(), mime_type="image/png", name="a.png"
        )
        out = describe([a])
        assert out.startswith("image:")
        assert "a.png" in out


class TestFromPath:
    @pytest.mark.asyncio
    async def test_non_image_returns_none(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x")
        assert attachments.from_path(str(f), str(tmp_path)) is None

    def test_missing_file_returns_none(self, tmp_path):
        assert attachments.from_path(str(tmp_path / "nope.png"), str(tmp_path)) is None


class TestClipboard:
    def test_from_clipboard_no_module(self, monkeypatch):
        # Force the import to fail.
        monkeypatch.setitem(sys.modules, "ctx.term.clipboard", None)
        assert attachments.from_clipboard() is None

    def test_clipboard_text_no_tools(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "ctx.term.clipboard", None)
        assert attachments.clipboard_text() == ""
