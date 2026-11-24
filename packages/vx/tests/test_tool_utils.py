"""Shared tool helpers: cancellation plumbing, path shortening, truncation."""

import asyncio

import pytest
from vx.tools._tool_utils import (
    ToolCancelledError,
    communicate_or_cancel,
    shorten_path,
    truncate_lines_by_bytes,
)


class TestShortenPath:
    def test_home_becomes_tilde(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        assert shorten_path(str(home / "x.py")) == "~/x.py"

    def test_other_path_untouched(self, tmp_path):
        assert shorten_path(str(tmp_path / "x.py")) == str(tmp_path / "x.py")


class TestTruncateLinesByBytes:
    def test_under_limit_untouched(self):
        text, truncated = truncate_lines_by_bytes(["a", "b"], 100)
        assert text == "a\nb"
        assert not truncated

    def test_over_limit_truncates_with_marker(self):
        text, truncated = truncate_lines_by_bytes(["x" * 50, "y" * 50], 60)
        assert truncated
        assert "[output truncated]" in text

    def test_exact_fit_not_truncated(self):
        text, truncated = truncate_lines_by_bytes(["ab", "cd"], 5)
        assert not truncated
        assert text == "ab\ncd"


class TestCommunicateOrCancel:
    @pytest.mark.asyncio
    async def test_normal_completion(self):
        proc = await asyncio.create_subprocess_exec(
            "echo", "hi", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await communicate_or_cancel(proc, None)
        assert out.strip() == b"hi"
        assert err == b""

    @pytest.mark.asyncio
    async def test_cancel_kills_process(self):
        proc = await asyncio.create_subprocess_exec(
            "sleep", "30", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        event = asyncio.Event()
        task = asyncio.create_task(communicate_or_cancel(proc, event))
        await asyncio.sleep(0.05)
        event.set()
        with pytest.raises(ToolCancelledError):
            await task
        assert proc.returncode is not None
