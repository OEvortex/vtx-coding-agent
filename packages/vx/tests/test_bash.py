"""Bash tool: output sanitizing, truncation, and the empty-command guard."""

import pytest
from vx.tools.bash import (
    MAX_OUTPUT_LINES,
    BashParams,
    BashTool,
    _sanitize_color_output,
    _sanitize_output,
    _truncate_tail,
)


class TestSanitize:
    def test_strips_ansi_and_control(self):
        raw = "\x1b[31mred\x1b[0m\r\nnext\x07bell"
        out = _sanitize_output(raw)
        assert "\x1b" not in out
        assert "\r" not in out
        assert "\x07" not in out
        assert "red" in out and "next" in out

    def test_color_output_keeps_sgr(self):
        raw = "\x1b[31mred\x1b[0m plain"
        out = _sanitize_color_output(raw)
        assert "\x1b[31m" in out
        assert "plain" in out

    def test_color_output_strips_non_sgr_escapes(self):
        raw = "\x1b[2Jclear\x1b[31mred"
        out = _sanitize_color_output(raw)
        assert "\x1b[2J" not in out
        assert "\x1b[31m" in out


class TestTruncateTail:
    def test_small_output_untouched(self):
        result = _truncate_tail("line1\nline2")
        assert not result.truncated
        assert result.content == "line1\nline2"

    def test_keeps_tail_lines(self):
        text = "\n".join(f"line{i}" for i in range(MAX_OUTPUT_LINES + 50))
        result = _truncate_tail(text)
        assert result.truncated
        assert "line0" not in result.content
        assert f"line{MAX_OUTPUT_LINES + 49}" in result.content
        assert result.lines_kept <= MAX_OUTPUT_LINES


class TestExecute:
    @pytest.mark.asyncio
    async def test_empty_command_fails(self):
        result = await BashTool().execute(BashParams(command="   "))
        assert not result.success

    @pytest.mark.asyncio
    async def test_runs_command(self):
        result = await BashTool().execute(BashParams(command="printf hello"))
        assert result.success
        assert "hello" in (result.result or "")

    @pytest.mark.asyncio
    async def test_nonzero_exit_fails(self):
        result = await BashTool().execute(BashParams(command="exit 3"))
        assert not result.success
        assert "3" in (result.ui_summary or "")
