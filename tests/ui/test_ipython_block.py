"""Tests for the prime-agent-style ``IpythonBlock`` widget used in RLM mode."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from vtx.ai.agent.tools.base import BaseTool
from vtx.ai.config import config
from vtx.tui.blocks import ToolBlock
from vtx.tui.chat import ChatLog
from vtx.tui.ipython_block import (
    TAG_DONE,
    TAG_ERROR,
    TAG_RESULT,
    TAG_STDERR,
    TAG_STDOUT,
    IpythonBlock,
    IpythonCellState,
)
from vtx.tui.styles import get_styles

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _TestApp(App):
    CSS = get_styles()

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")


def _make_block(call_msg: str = "print(2 + 2)") -> IpythonBlock:
    """Build a bare IpythonBlock for unit-style tests."""
    block = IpythonBlock(name="ipython", call_msg=call_msg, icon=">>>", tool=None)
    block.__dict__["call_after_refresh"] = lambda callback: callback()
    return block


def _noop_tool() -> BaseTool:
    """Return a stub BaseTool for the chat-log wiring tests."""

    class _StubTool(BaseTool):
        name = "ipython"
        description = "stub"
        params = type("P", (), {"model_fields": {}})

        async def execute(self, params, cancel_event=None, tool_call_id=None, on_output=None):
            from vtx.core.types import ToolResult

            return ToolResult(success=True, result="", ui_summary="")

        def format_call(self, params):
            return ""

    return _StubTool()


# ---------------------------------------------------------------------------
# Header formatting
# ---------------------------------------------------------------------------


class TestIpythonBlockHeader:
    def test_marker_is_pulse_when_partial(self):
        block = _make_block()
        marker, _style = block._marker()
        assert marker in ("◇", "◈", "◆")

    def test_marker_is_check_on_success(self):
        block = _make_block()
        block._success = True
        marker, _style = block._marker()
        assert marker == "✓"

    def test_marker_is_cross_on_error(self):
        block = _make_block()
        block._success = False
        marker, _style = block._marker()
        assert marker == "✗"

    def test_preview_is_first_non_blank_line(self):
        block = _make_block(call_msg="\n# comment\nx = 1\ny = 2")
        text = block._build_header_text()
        assert "x = 1" in text.plain

    def test_preview_truncated_to_80_chars(self):
        long = "variable_with_very_long_name_and_many_operations_chained_together_in_single_expression = compute_results_and_transform_data_for_display(item_a, item_b, item_c)"
        block = _make_block(call_msg=long)
        text = block._build_header_text()
        assert "…" in text.plain

    def test_waiting_for_code_when_empty(self):
        block = IpythonBlock(name="ipython", call_msg=None, icon=">>>")
        text = block._build_header_text()
        assert "waiting for code" in text.plain

    def test_expand_hint_in_header(self):
        block = _make_block()
        text = block._build_header_text()
        assert "ctrl+o to expand" in text.plain

    def test_error_name_in_header(self):
        block = _make_block()
        block._cell_state.is_error = True
        block._cell_state.ename = "NameError"
        text = block._build_header_text()
        assert "NameError" in text.plain


# ---------------------------------------------------------------------------
# Streaming parser
# ---------------------------------------------------------------------------


class TestIpythonBlockStreaming:
    @pytest.mark.asyncio
    async def test_stdout_block_appended(self):
        block = _make_block()
        async with _TestApp().run_test() as pilot:
            pilot.app.mount(block)
            await pilot.pause()
            block.append_live_output(TAG_STDOUT)
            block.append_live_output(f"{TAG_STDOUT}hello\nworld")
            block.set_expanded(True)
            await pilot.pause()
            assert len(block._cell_state.content) == 1
            assert block._cell_state.content[0].kind == "stdout"
            assert block._cell_state.content[0].text == "hello\nworld"

    def test_stderr_block_routes_to_stderr_kind(self):
        block = _make_block()
        block.append_live_output(f"{TAG_STDERR}warning: something")
        assert block._cell_state.content[-1].kind == "stderr"

    def test_result_block_stored(self):
        block = _make_block()
        block.append_live_output(f"{TAG_RESULT}42")
        assert block._cell_state.result_repr == "42"
        assert block._cell_state.content[-1].kind == "result"

    def test_error_sets_ename_and_marks_error(self):
        block = _make_block()
        block.append_live_output(
            f"{TAG_ERROR}NameError: name 'x' is not defined\nTraceback line 1"
        )
        assert block._cell_state.is_error is True
        assert block._cell_state.ename == "NameError"
        assert block._cell_state.content[-1].kind == "error"

    def test_done_stops_partial(self):
        block = _make_block()
        assert block._cell_state.is_partial is True
        block.append_live_output(TAG_DONE)
        assert block._cell_state.is_partial is False

    def test_plain_delta_treated_as_stdout(self):
        block = _make_block()
        block.append_live_output("hello world\n")
        assert block._cell_state.content[-1].kind == "stdout"
        assert block._cell_state.content[-1].text == "hello world\n"

    def test_empty_stdout_skipped(self):
        block = _make_block()
        block.append_live_output(TAG_STDOUT)
        assert block._cell_state.content == []


# ---------------------------------------------------------------------------
# set_result / expansion
# ---------------------------------------------------------------------------


class TestIpythonBlockResult:
    def test_set_result_success_clears_partial(self):
        block = _make_block()
        block.set_result("summary", "details", True)
        assert block._cell_state.is_partial is False
        assert block._success is True
        assert block._cell_state.finished_at is not None

    def test_set_result_failure_marks_error(self):
        block = _make_block()
        block.set_result("oops", "details", False)
        assert block._cell_state.is_error is True
        assert block._cell_state.ename == "Error"

    def test_set_expanded_toggles(self):
        block = _make_block()
        assert block._cell_state.expanded is False
        block.set_expanded(True)
        assert block._cell_state.expanded is True
        assert block._expanded is True

    def test_set_expanded_is_idempotent(self):
        block = _make_block()
        block.set_expanded(True)
        block.set_expanded(True)
        assert block._cell_state.expanded is True


# ---------------------------------------------------------------------------
# Inherit + lazy ui_block property on IpythonTool
# ---------------------------------------------------------------------------


class TestIpythonBlockWiring:
    @pytest.mark.asyncio
    async def test_chatlog_uses_ipython_block_when_tool_provides_one(self, monkeypatch):
        from vtx.ai.agent.tools.ipython import IpythonTool

        tool = IpythonTool()
        monkeypatch.setattr(config, "mode", "rlm")
        async with _TestApp().run_test() as pilot:
            chat = pilot.app.query_one("#chat-log", ChatLog)
            block = chat.start_tool("ipython", "id-x", "print(1)", icon=">>>", tool=tool)
            await pilot.pause()
            assert isinstance(block, IpythonBlock)
            assert isinstance(block, ToolBlock)

    @pytest.mark.asyncio
    async def test_chatlog_uses_plain_toolblock_when_mode_not_rlm(self, monkeypatch):
        from vtx.ai.agent.tools.ipython import IpythonTool

        tool = IpythonTool()
        monkeypatch.setattr(config, "mode", "tool_first")
        async with _TestApp().run_test() as pilot:
            chat = pilot.app.query_one("#chat-log", ChatLog)
            block = chat.start_tool("ipython", "id-y", "print(1)", icon=">>>", tool=tool)
            await pilot.pause()
            assert type(block) is ToolBlock
            assert not isinstance(block, IpythonBlock)


# ---------------------------------------------------------------------------
# IpythonCellState helper
# ---------------------------------------------------------------------------


class TestIpythonCellState:
    def test_line_counts_none_when_empty(self):
        state = IpythonCellState(code="", content=[], is_partial=False)
        assert state.line_counts() is None

    def test_line_counts_with_code(self):
        state = IpythonCellState(code="x = 1\ny = 2", content=[], is_partial=True)
        assert state.line_counts() == (2, 0)

    def test_line_counts_with_output(self):
        state = IpythonCellState(code="x = 1", content=[], is_partial=True)
        from vtx.tui.ipython_block import IpythonCellContentBlock

        state.content.append(IpythonCellContentBlock(kind="stdout", text="hi\nthere\nfriend"))
        assert state.line_counts() == (1, 3)
