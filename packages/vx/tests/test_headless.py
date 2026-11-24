"""Headless mode: prompt resolution, exit codes, run rendering."""

import asyncio
import io

import pytest
from ctx.core.events import (
    AgentEndEvent,
    ErrorEvent,
    ToolApprovalEvent,
    ToolStartEvent,
    TurnEndEvent,
)
from ctx.protocol.types import AssistantMessage, StopReason, TextContent
from vx.headless import _exit_code, _one_line, render_run, resolve_prompt


class TestResolvePrompt:
    def test_literal(self):
        assert resolve_prompt("hello", stdin=io.StringIO("stdin")) == "hello"

    def test_dash_reads_stdin(self):
        assert resolve_prompt("-", stdin=io.StringIO("  piped prompt  ")) == "piped prompt"

    def test_strips(self):
        assert resolve_prompt("  hi  ", stdin=io.StringIO("")) == "hi"


class TestExitCode:
    def test_known_reasons(self):
        assert _exit_code(StopReason.STOP) == 0
        assert _exit_code(StopReason.ERROR) == 1
        assert _exit_code(StopReason.LENGTH) == 3

    def test_unknown_defaults_one(self):
        from typing import cast

        assert _exit_code(cast(StopReason, "weird")) == 1


class TestOneLine:
    def test_flattens_whitespace(self):
        assert _one_line("a\nb   c") == "a b c"

    def test_truncates_long(self):
        out = _one_line("x" * 200)
        assert len(out) <= 96
        assert out.endswith("…")


async def _aiter(events):
    for e in events:
        yield e


class TestRenderRun:
    @pytest.mark.asyncio
    async def test_prints_final_text_on_stop(self):
        msg = AssistantMessage(content=[TextContent(text="the answer")])
        events = [TurnEndEvent(assistant_message=msg), AgentEndEvent(stop_reason=StopReason.STOP)]
        out, err = io.StringIO(), io.StringIO()
        stop = await render_run(_aiter(events), out=out, err=err)
        assert stop == StopReason.STOP
        assert out.getvalue().strip() == "the answer"

    @pytest.mark.asyncio
    async def test_error_stop_prints_nothing(self):
        msg = AssistantMessage(content=[TextContent(text="partial")])
        events = [TurnEndEvent(assistant_message=msg), AgentEndEvent(stop_reason=StopReason.ERROR)]
        out, err = io.StringIO(), io.StringIO()
        stop = await render_run(_aiter(events), out=out, err=err)
        assert stop == StopReason.ERROR
        assert out.getvalue() == ""

    @pytest.mark.asyncio
    async def test_error_event_goes_to_stderr(self):
        events = [ErrorEvent(error="boom"), AgentEndEvent(stop_reason=StopReason.ERROR)]
        out, err = io.StringIO(), io.StringIO()
        await render_run(_aiter(events), out=out, err=err)
        assert "boom" in err.getvalue()

    @pytest.mark.asyncio
    async def test_approval_denied(self):
        fut = asyncio.get_event_loop().create_future()
        events = [
            ToolApprovalEvent(tool_name="bash", future=fut),
            AgentEndEvent(stop_reason=StopReason.STOP),
        ]
        out, err = io.StringIO(), io.StringIO()
        await render_run(_aiter(events), out=out, err=err)
        assert fut.done()
        assert "denied" in err.getvalue()

    @pytest.mark.asyncio
    async def test_verbose_tool_start(self):
        events = [ToolStartEvent(tool_name="bash"), AgentEndEvent(stop_reason=StopReason.ERROR)]
        out, err = io.StringIO(), io.StringIO()
        await render_run(_aiter(events), out=out, err=err, verbose=True)
        assert "bash" in err.getvalue()
