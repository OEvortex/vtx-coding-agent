"""The ``codemode`` notebook block.

The point under test is that a codemode call reads as an execution rather than
as a one-line receipt: the script is on screen, the return value is on screen,
and ``ctrl+o`` opens the per-call trace. Everything goes through
:meth:`ChatLog.set_tool_result`, which is the path the runner and session replay
both take, so the ``ui_data`` hand-off is covered by the same assertions.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from vtx.coding_agent.tools.codemode import CodemodeTool
from vtx.tui.blocks import ToolBlock
from vtx.tui.chat import ChatLog
from vtx.tui.codemode_block import CODE_LINES, CodemodeToolBlock
from vtx.tui.styles import get_styles

FIRST_LINE = "rows = await tools.search(query='find dead code')"
SCRIPT = f"{FIRST_LINE}\nreturn [r['name'] for r in rows]"


def _payload(**overrides) -> dict:
    data = {
        "code": SCRIPT,
        "value": '["edit", "grep", "read"]',
        "printed": ["3 tools"],
        "calls": [
            {"name": "search", "ok": True, "kind": None, "message": None, "duration_ms": 412},
            {"name": "read", "ok": True, "kind": None, "message": None, "duration_ms": 90},
            {
                "name": "bash",
                "ok": False,
                "kind": "tool_failure",
                "message": "exit 1",
                "duration_ms": 1200,
            },
        ],
        "error": None,
        "images": 0,
        "output_truncated": False,
        "duration_ms": 1234,
    }
    data.update(overrides)
    return data


class _TestApp(App):
    CSS = get_styles()

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")


@pytest.fixture(autouse=True)
def _reset_counter() -> None:
    # The counter is process-wide by design; tests would otherwise depend on
    # execution order to know which number the next cell gets.
    CodemodeToolBlock.execution_count = 0


def _header(block: CodemodeToolBlock) -> str:
    return block.query_one("#tool-header").visual.plain


def _body(block: CodemodeToolBlock) -> str:
    return block.query_one("#tool-output").visual.plain


async def _start(pilot, tool: CodemodeTool, code: str = SCRIPT) -> CodemodeToolBlock:
    chat = pilot.app.query_one("#chat-log", ChatLog)
    params = tool.params(code=code)
    chat.start_tool("codemode", "t1", tool.format_call(params), icon=tool.tool_icon, tool=tool)
    await pilot.pause()
    return chat._tool_blocks["t1"]


def _finish(pilot, block, *, success: bool = True, ui_data: dict | None = None, summary=None):
    chat = pilot.app.query_one("#chat-log", ChatLog)
    chat.set_tool_result("t1", summary, None, success, ui_data=ui_data)


class TestBlockSelection:
    @pytest.mark.asyncio
    async def test_codemode_gets_its_own_block(self):
        assert CodemodeTool().ui_block is CodemodeToolBlock

    @pytest.mark.asyncio
    async def test_every_other_tool_keeps_the_default_block(self):
        # A codemode cell is a notebook cell, but every other tool keeps the
        # plain receipt; this is what stops the registry growing a custom block
        # per tool.
        async with _TestApp().run_test() as pilot:
            chat = pilot.app.query_one("#chat-log", ChatLog)
            block = chat.start_tool("read", "r1", "some/file.py", icon="→")
            assert type(block) is ToolBlock


class TestPendingCell:
    @pytest.mark.asyncio
    async def test_script_shows_while_it_runs(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            # ToolEndEvent lands before execution, which is when the script
            # becomes known. Showing it there is the difference between a cell
            # and a receipt that pops in at the end.
            block.update_call_msg(SCRIPT)
            await pilot.pause()
            assert "running" in _header(block)
            assert FIRST_LINE in _body(block)


class TestFinishedCell:
    @pytest.mark.asyncio
    async def test_in_and_out_prompts_frame_the_cell(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(value=None), summary="Script ok (3 tool calls)")
            await pilot.pause()
            assert _header(block).startswith("⟩ In [1]:")
            assert "3 tool calls" in _header(block)
            body = _body(block)
            assert body.startswith("In [1]: ")
            # Continuation lines align under the code, not under the prompt.
            assert "\n        return [r['name'] for r in rows]" in body
            assert "3 tools" in body

    @pytest.mark.asyncio
    async def test_returned_value_is_rendered_under_its_own_prompt(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload())
            await pilot.pause()
            body = _body(block)
            assert "Out [1]: " in body
            # Short enough to fold, so it reads as the repr IPython would print
            # rather than as the multi-line JSON the model consumes.
            assert '["edit", "grep", "read"]' in body

    @pytest.mark.asyncio
    async def test_call_trace_is_the_summary(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload())
            await pilot.pause()
            assert "search 0.4s" in _body(block)
            assert "bash 1.2s (tool_failure)" in _body(block)

    @pytest.mark.asyncio
    async def test_expansion_shows_each_call(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload())
            block.set_expanded(True)
            await pilot.pause()
            body = _body(block)
            assert "✓ search" in body
            assert "✗ bash" in body
            assert "exit 1" in body

    @pytest.mark.asyncio
    async def test_a_long_script_is_clipped_with_a_hint(self):
        long_script = "\n".join(f"x{i} = {i}" for i in range(CODE_LINES * 2))
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool(), code=long_script)
            _finish(pilot, block, ui_data=_payload(code=long_script))
            await pilot.pause()
            body = _body(block)
            assert "ctrl+o" in body
            assert "x0 = 0" in body
            assert f"x{CODE_LINES * 2 - 1}" not in body
            block.set_expanded(True)
            await pilot.pause()
            assert f"x{CODE_LINES * 2 - 1} = {CODE_LINES * 2 - 1}" in _body(block)

    @pytest.mark.asyncio
    async def test_cells_number_in_order(self):
        async with _TestApp().run_test() as pilot:
            chat = pilot.app.query_one("#chat-log", ChatLog)
            first = await _start(pilot, CodemodeTool())
            chat.set_tool_result("t1", "ok", None, True, ui_data=_payload())
            chat.start_tool("codemode", "t2", SCRIPT, icon="⟩", tool=CodemodeTool())
            await pilot.pause()
            second = chat._tool_blocks["t2"]
            chat.set_tool_result("t2", "ok", None, True, ui_data=_payload())
            await pilot.pause()
            assert _header(first).startswith("⟩ In [1]:")
            assert _header(second).startswith("⟩ In [2]:")


class TestFailedCell:
    @pytest.mark.asyncio
    async def test_error_is_under_the_source(self):
        error = {
            "kind": "script",
            "message": "NameError: name 'toolz' is not defined",
            "stack": 'Traceback (most recent call last):\n  File "<codemode>", line 1\nNameError',
        }
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(
                pilot,
                block,
                success=False,
                ui_data=_payload(value=None, printed=[], error=error),
                summary="Script failed (script) after 1 calls (not undone)",
            )
            await pilot.pause()
            assert "Script failed" in _header(block)
            body = _body(block)
            assert FIRST_LINE in body
            assert "script: NameError" in body
            assert "Traceback" in body
            # A failure has no Out prompt: there is no value.
            assert "Out [1]:" not in body


class TestFallback:
    @pytest.mark.asyncio
    async def test_a_session_without_a_payload_still_shows_the_script(self):
        # Sessions written before the payload existed carry no ui_data. The body
        # still renders the cell from the call text, minus the Out prompt.
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            chat = pilot.app.query_one("#chat-log", ChatLog)
            chat.set_tool_result("t1", "script: rows = ...", None, True)
            await pilot.pause()
            assert "return [r['name'] for r in rows]" in _body(block)
            assert "Out [1]:" not in _body(block)
