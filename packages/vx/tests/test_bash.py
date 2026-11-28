"""Bash tool: output sanitizing, truncation, and commands that never end."""

import asyncio
import time

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
    async def test_pipeline_output_does_not_stall_command_completion(self):
        chunks = []
        result = await BashTool().execute(
            BashParams(command="printf hello | cat", timeout=2), on_output=chunks.append
        )
        assert result.success
        assert "hello" in "".join(chunks)

    @pytest.mark.asyncio
    async def test_nonzero_exit_fails(self):
        result = await BashTool().execute(BashParams(command="exit 3"))
        assert not result.success
        assert "3" in (result.ui_summary or "")


class TestNotStuck:
    """A command that leaves something behind must not freeze the transcript.

    asyncio's ``Process.wait()`` resolves only once every pipe is closed, so a
    background child holding stdout keeps it -- and the turn -- waiting until
    the timeout. These pin the behaviour that replaced it.
    """

    @pytest.mark.asyncio
    async def test_a_background_child_does_not_hold_up_the_call(self):
        started = time.monotonic()
        result = await BashTool().execute(BashParams(command="sleep 60 & echo up", timeout=30))
        assert time.monotonic() - started < 5
        assert result.success
        assert "up" in (result.result or "")

    @pytest.mark.asyncio
    async def test_the_background_child_stays_with_the_shell(self, tmp_path):
        """The shell outlives the call, so its `&` children do too.

        That is the price of cwd and exports surviving between calls; a process
        that must belong to the tool is a `background=true` task, which has an
        id and a kill.
        """
        tool = BashTool()
        result = await tool.execute(BashParams(command="sleep 60 & echo up", timeout=30))
        assert result.success
        jobs = await tool.execute(BashParams(command="jobs", timeout=30))
        assert "sleep 60" in (jobs.result or "")
        await tool.execute(BashParams(command="kill %1", timeout=30))

    @pytest.mark.asyncio
    async def test_one_long_line_is_output_not_an_error(self):
        # Over the reader's 64 KiB line default: minified JSON, a base64 blob.
        result = await BashTool().execute(
            BashParams(command="head -c 200000 /dev/zero | tr '\\0' a", timeout=30)
        )
        assert result.success
        assert "Separator is not found" not in (result.ui_summary or "")
        assert len(result.result or "") > 1000

    @pytest.mark.asyncio
    async def test_the_timeout_still_reports(self):
        started = time.monotonic()
        result = await BashTool().execute(BashParams(command="sleep 60", timeout=1))
        assert time.monotonic() - started < 5
        assert not result.success
        assert "timed out" in (result.ui_summary or "")

    @pytest.mark.asyncio
    async def test_escape_aborts_a_running_command(self):
        cancel = asyncio.Event()

        async def press() -> None:
            await asyncio.sleep(0.2)
            cancel.set()

        task = asyncio.create_task(press())
        assert task
        started = time.monotonic()
        result = await BashTool().execute(
            BashParams(command="sleep 60", timeout=30), cancel_event=cancel
        )
        assert time.monotonic() - started < 3
        assert not result.success
        assert "aborted" in (result.ui_summary or "")
