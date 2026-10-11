"""The ``codemode`` tool block.

The point under test is that a codemode call reads as an execution rather than
as a one-line receipt: the script is on screen, its output is on screen, and
``ctrl+o`` opens the per-call trace. Everything goes through
:meth:`ChatLog.set_tool_result`, which is the path the runner and session replay
both take, so the ``ui_data`` hand-off is covered by the same assertions.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from vtx.coding_agent.tools.codemode import CodemodeTool
from vtx.tui.blocks import ToolBlock
from vtx.tui.chat import ChatLog
from vtx.tui.codemode_block import CALL_PREVIEW_COUNT, CODE_LINES, CodemodeToolBlock
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
        # A codemode call gets the script-the-call treatment, but every other
        # tool keeps the plain receipt; this is what stops the registry growing
        # a custom block per tool.
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
            # becomes known. Showing it there is the difference between a
            # script that appears and a receipt that pops in at the end.
            block.update_call_msg(SCRIPT)
            await pilot.pause()
            assert "running" in _header(block)
            assert FIRST_LINE in _body(block)


class TestFinishedCell:
    @pytest.mark.asyncio
    async def test_the_header_names_the_tool_and_what_it_did(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(value=None), summary="Script ok (3 tool calls)")
            await pilot.pause()
            # No IPython numbering: the header reads like every other tool
            # block, and the body carries the run.
            assert _header(block).startswith("⟩ codemode")
            assert "In [" not in _header(block)
            assert "3 tool calls" in _header(block)

    @pytest.mark.asyncio
    async def test_the_script_leads_the_body(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(value=None), summary="Script ok (3 tool calls)")
            await pilot.pause()
            body = _body(block)
            assert body.startswith("  ")
            assert FIRST_LINE in body
            # Continuation lines align under the code, not under a prompt.
            assert "\n  return [r['name'] for r in rows]" in body
            assert "3 tools" in body

    @pytest.mark.asyncio
    async def test_output_is_compact_by_default(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload())
            await pilot.pause()
            body = _body(block)
            # Printed output and the returned value share one compact section;
            # there is no "Out" prompt echoing the value back.
            assert '["edit", "grep", "read"]' in body
            assert "Out [" not in body
            # Short values fold onto the line they belong on.
            assert "\n    " not in body

    @pytest.mark.asyncio
    async def test_long_output_is_clipped_with_a_hint(self):
        long_value = "\n".join(f"line {i}" for i in range(30))
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(value=long_value))
            await pilot.pause()
            body = _body(block)
            assert "ctrl+o" in body
            assert "line 0" in body
            assert "line 29" not in body
            block.set_expanded(True)
            await pilot.pause()
            assert "line 29" in _body(block)

    @pytest.mark.asyncio
    async def test_calls_are_a_checklist_with_durations(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload())
            await pilot.pause()
            body = _body(block)
            assert "search" in body
            assert "read" in body
            assert "bash" in body
            assert "0.4s" in body
            assert "1.2s" in body
            # A failed call names both why it failed and how it failed.
            assert "exit 1" in body
            assert "tool_failure" in body

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
    async def test_many_calls_collapse_to_the_recent_ones(self):
        many = [
            {"name": f"t{i}", "ok": True, "kind": None, "message": None, "duration_ms": 10 * i}
            for i in range(CALL_PREVIEW_COUNT * 2)
        ]
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(calls=many))
            await pilot.pause()
            body = _body(block)
            assert "earlier calls" in body
            assert "ctrl+o" in body
            assert "t0 " not in body
            assert f"t{CALL_PREVIEW_COUNT * 2 - 1}" in body
            block.set_expanded(True)
            await pilot.pause()
            assert "t0 " in _body(block)

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


class TestFallback:
    @pytest.mark.asyncio
    async def test_a_session_without_a_payload_still_shows_the_script(self):
        # Sessions written before the payload existed carry no ui_data. The body
        # still renders the block from the call text.
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            chat = pilot.app.query_one("#chat-log", ChatLog)
            chat.set_tool_result("t1", "script: rows = ...", None, True)
            await pilot.pause()
            assert "return [r['name'] for r in rows]" in _body(block)
