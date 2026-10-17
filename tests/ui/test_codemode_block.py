"""The ``codemode`` tool block.

The point under test is that a codemode call reads as an execution rather than
as a one-line receipt: the script is on screen, its nested calls are on screen
with their durations, and its output is on screen — all of it, because the block
clips nothing. Everything goes through :meth:`ChatLog.set_tool_result`, which is
the path the runner and session replay both take, so the ``ui_data`` hand-off is
covered by the same assertions.
"""

from __future__ import annotations

import json

import pytest
from textual.app import App, ComposeResult

from vtx.coding_agent.tools.codemode import PROGRESS_SENTINEL, CodemodeTool
from vtx.tui.blocks import ToolBlock
from vtx.tui.chat import ChatLog
from vtx.tui.codemode_block import SECTION_INDENT, CodemodeToolBlock
from vtx.tui.styles import get_styles
from vtx.tui.task_ui import GLYPHS

FIRST_LINE = "rows = await tools.search(query='find dead code')"
SCRIPT = f"{FIRST_LINE}\nreturn [r['name'] for r in rows]"


def _call(name: str, *, ok: bool = True, ms: int | None = 100, args: str | None = None, **extra):
    """One nested call, in the shape the tool's ``ui_data`` now carries."""
    return {
        "name": name,
        "ok": ok,
        "status": extra.pop("status", "ok" if ok else "error"),
        "kind": extra.pop("kind", None),
        "message": extra.pop("message", None),
        "duration_ms": ms,
        "args": args,
        **extra,
    }


def _payload(**overrides) -> dict:
    data = {
        "code": SCRIPT,
        "value": '["edit", "grep", "read"]',
        "printed": ["3 tools"],
        "calls": [
            _call("search", ms=412, args='{"query":"find dead code"}'),
            _call("read", ms=90, args='{"path":"pyproject.toml"}'),
            _call("bash", ok=False, ms=1200, kind="tool_failure", message="exit 1"),
        ],
        "error": None,
        "images": 0,
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
            assert "1.2s" in _header(block)

    @pytest.mark.asyncio
    async def test_sections_are_labelled_and_indented(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(), summary="Script ok (3 tool calls)")
            await pilot.pause()
            body = _body(block)
            for section in ("script", "calls", "output", "value"):
                assert f"⎿ {section}" in body
            # Every section marker sits in the same column as every other tool
            # detail in the scrollback.
            markers = [line for line in body.split("\n") if "⎿" in line]
            assert markers and all(line.startswith("  ⎿ ") for line in markers)
            # Content is one step in from its label, never flush with it.
            assert f"\n{SECTION_INDENT}1  {FIRST_LINE}" in body

    @pytest.mark.asyncio
    async def test_the_script_is_its_own_section(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(value=None), summary="Script ok (3 tool calls)")
            await pilot.pause()
            body = _body(block)
            script_at = body.index("⎿ script")
            assert FIRST_LINE in body[script_at:]
            # Continuation lines align under the code, not under a prompt, and
            # the line number keeps its own column.
            assert f"\n{SECTION_INDENT}1  {FIRST_LINE}" in body
            assert f"\n{SECTION_INDENT}2  return [r['name'] for r in rows]" in body
            assert "3 tools" in body

    @pytest.mark.asyncio
    async def test_printed_output_and_returned_value_are_separate(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(), summary="Script ok (3 tool calls)")
            await pilot.pause()
            body = _body(block)
            # A reader has to be able to tell what the script printed from what
            # it returned; they are different claims about the run.
            assert body.index("⎿ output") < body.index("3 tools")
            assert body.index("⎿ value") < body.index('["edit", "grep", "read"]')
            # A short pretty-printed value folds onto the one line it belongs
            # on rather than becoming three.
            value_section = body.split("⎿ value")[1]
            assert value_section.strip() == '["edit", "grep", "read"]'

    @pytest.mark.asyncio
    async def test_long_output_previews_and_expands(self):
        long_value = "\n".join(f"line {i}" for i in range(30))
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(printed=[long_value]))
            await pilot.pause()
            body = _body(block)
            # Thirty lines of print output is the case the preview exists for:
            # shown whole, it buries the script and the call list above it.
            assert "line 0" in body
            assert "line 29" not in body
            assert "ctrl+o to expand" in body
            assert "25 lines hidden" in body

            block.set_expanded(True)
            await pilot.pause()
            expanded = _body(block)
            assert "line 29" in expanded
            assert "hidden" not in expanded

    @pytest.mark.asyncio
    async def test_a_long_script_previews_and_expands(self):
        long_script = "\n".join(f"x{i} = {i}" for i in range(120))
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool(), code=long_script)
            _finish(pilot, block, ui_data=_payload(code=long_script))
            await pilot.pause()
            body = _body(block)
            assert "x0 = 0" in body
            assert "x119 = 119" not in body
            assert "ctrl+o to expand" in body

            block.set_expanded(True)
            await pilot.pause()
            assert "x119 = 119" in _body(block)

    @pytest.mark.asyncio
    async def test_the_script_is_numbered_so_a_traceback_is_findable(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(value=None))
            await pilot.pause()
            body = _body(block)
            # The error section quotes `line 12`; a script drawn without numbers
            # makes that unanswerable without counting by hand.
            script = body.split("⎿ script")[1].split("⎿ ")[0]
            numbers = [
                line.split()[0]
                for line in script.split("\n")
                if line.strip() and line.strip()[0].isdigit()
            ]
            assert numbers[:2] == ["1", "2"]

    @pytest.mark.asyncio
    async def test_a_call_shows_what_it_was_called_with(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload())
            await pilot.pause()
            body = _body(block)
            # Two greps in a row are the same line twice without arguments. The
            # arguments are the only thing that tells the calls apart.
            assert '{"query":"find dead code"}' in body
            assert '{"path":"pyproject.toml"}' in body

    @pytest.mark.asyncio
    async def test_short_arguments_are_still_drawn(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(calls=[_call("read", ms=5, args='{"p":"a"}')]))
            await pilot.pause()
            # `{"p":"a"}` is eight characters, under the width below which a
            # column is refused. Refusing to draw it because it is *short*
            # throws away the only thing that distinguishes two calls.
            assert '{"p":"a"}' in _body(block)

    @pytest.mark.asyncio
    async def test_long_arguments_are_elided_rather_than_wrapped(self):
        blob = '{"pattern":"' + "x" * 300 + '"}'
        async with _TestApp().run_test(size=(90, 60)) as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(calls=[_call("grep", ms=5, args=blob)]))
            await pilot.pause()
            rows = [
                line
                for line in _body(block).split("\n")
                if GLYPHS["success"] in line and "grep" in line
            ]
            assert len(rows) == 1, "arguments must not wrap the row onto a second line"
            assert "…" in rows[0]

    @pytest.mark.asyncio
    async def test_the_call_list_keeps_its_tail(self):
        many = [_call(f"t{i}", ms=10 * i, args=f'{{"i":{i}}}') for i in range(40)]
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(calls=many))
            await pilot.pause()
            body = _body(block)
            # A script that fails usually fails near its tail, so the tail is
            # exactly what a head-first preview would have hidden.
            assert "t0 " not in body
            assert "t39 " in body
            assert "32 earlier calls" in body

            block.set_expanded(True)
            await pilot.pause()
            expanded = _body(block)
            assert "t0 " in expanded
            assert "earlier calls" not in expanded

    @pytest.mark.asyncio
    async def test_calls_are_a_checklist_with_durations(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload())
            await pilot.pause()
            body = _body(block)
            assert "✓  search" in body
            assert "✓  read" in body
            assert "✗  bash" in body
            assert "0.4s" in body
            assert "1.2s" in body
            # A failed call names both why it failed and how it failed.
            assert "exit 1 · tool_failure" in body

    @pytest.mark.asyncio
    async def test_durations_line_up_as_a_column(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(
                pilot,
                block,
                ui_data=_payload(
                    calls=[
                        {
                            "name": "a",
                            "ok": True,
                            "kind": None,
                            "message": None,
                            "duration_ms": 40,
                        },
                        {
                            "name": "b",
                            "ok": True,
                            "kind": None,
                            "message": None,
                            "duration_ms": 4000,
                        },
                    ]
                ),
            )
            await pilot.pause()
            rows = [
                line for line in _body(block).split("\n") if line.strip().startswith(("✓", "✗"))
            ]
            assert len(rows) == 2
            # Same right edge for the timing column, so the numbers read as a
            # column rather than as trailing noise.
            assert rows[0].index("0.0s") == rows[1].index("4.0s")
            assert rows[0].index("0.0s") + len("0.0s") == rows[1].index("4.0s") + len("4.0s")


class TestFailedCell:
    @pytest.mark.asyncio
    async def test_error_is_its_own_section_under_the_source(self):
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
            assert "⎿ error" in body
            assert "script · NameError" in body
            assert "Traceback" in body

    @pytest.mark.asyncio
    async def test_the_whole_traceback_is_shown(self):
        stack = "\n".join(f'  File "<codemode>", line {i}, in <module>' for i in range(30))
        error = {"kind": "script", "message": "boom", "stack": stack}
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(
                pilot,
                block,
                success=False,
                ui_data=_payload(value=None, printed=[], error=error),
                summary="Script failed (script)",
            )
            await pilot.pause()
            body = _body(block)
            assert "line 0" in body
            assert "line 29" in body
            assert "hidden" not in body


class TestNarrowTerminals:
    """A block that wraps correctly is the one that stays readable.

    The cap removal is what makes this matter: previously a long line was
    clipped rather than wrapped, so nothing in this block ever had to survive
    a narrow terminal.
    """

    @pytest.mark.asyncio
    async def test_no_rendered_line_exceeds_the_block(self):
        long_word = "x" * 300
        long_out = "scanned a great many files and found " + long_word
        async with _TestApp().run_test(size=(70, 60)) as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(
                pilot,
                block,
                ui_data=_payload(
                    code="results = " + long_word,
                    printed=[long_out],
                    value=long_out,
                    calls=[
                        {
                            "name": "an_extremely_long_tool_name_here",
                            "ok": False,
                            "kind": "tool_failure",
                            "message": (
                                "a failure message that is quite long indeed and keeps going"
                            ),
                            "duration_ms": 123,
                        }
                    ],
                    error={
                        "kind": "script",
                        "message": "RuntimeError: " + "why " * 40,
                        "stack": "",
                    },
                ),
            )
            await pilot.pause()
            await pilot.pause()
            width = block.query_one("#tool-output").size.width
            assert width, "the block must be laid out before its width means anything"
            for line in _body(block).split("\n"):
                assert len(line) <= width, f"line overflows {width} columns: {line!r}"

    @pytest.mark.asyncio
    async def test_a_wrapped_line_keeps_its_section_indent(self):
        async with _TestApp().run_test(size=(70, 60)) as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(
                pilot, block, ui_data=_payload(printed=[" ".join(f"word{i}" for i in range(40))])
            )
            await pilot.pause()
            await pilot.pause()
            output = _body(block).split("⎿ output")[1].split("⎿ value")[0]
            lines = [line for line in output.split("\n") if line.strip()]
            assert len(lines) > 1, "this output should have had to wrap"
            # The wrapped remainder hangs under its own content, not back at the
            # block's margin where a bare Label would have put it.
            assert all(line.startswith(SECTION_INDENT) for line in lines)

    @pytest.mark.asyncio
    async def test_a_failure_reason_hangs_under_its_own_row(self):
        async with _TestApp().run_test(size=(70, 60)) as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(
                pilot,
                block,
                ui_data=_payload(
                    calls=[
                        {
                            "name": "read",
                            "ok": False,
                            "kind": "tool_failure",
                            "message": " ".join(f"reason{i}" for i in range(30)),
                            "duration_ms": 10,
                        }
                    ]
                ),
            )
            await pilot.pause()
            await pilot.pause()
            rows = [
                line for line in _body(block).split("\n") if "read" in line or "reason" in line
            ]
            assert len(rows) > 1, "this reason should have had to wrap"
            # Every row is indented at least as far as the glyph it belongs to.
            assert all(line.startswith(SECTION_INDENT) for line in rows)


class TestAttachments:
    @pytest.mark.asyncio
    async def test_attached_images_are_counted(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(images=2))
            await pilot.pause()
            assert "2 images attached to the result" in _body(block)

    @pytest.mark.asyncio
    async def test_a_single_image_reads_as_one(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(images=1))
            await pilot.pause()
            assert "1 image attached to the result" in _body(block)


class TestLiveCalls:
    """A run that says nothing for ninety seconds is indistinguishable from hung.

    The host reports each nested call twice -- admitted, then finished -- and
    these are the tests that the pair lands as one row that changes shape rather
    than two rows, and that an in-flight call is visibly in flight.
    """

    @staticmethod
    def _frame(**fields) -> str:
        base = {
            "name": "read",
            "status": "running",
            "args": None,
            "kind": None,
            "message": None,
            "duration_ms": None,
        }
        base.update(fields)
        return f"{PROGRESS_SENTINEL}{json.dumps(base)}\n"

    @pytest.mark.asyncio
    async def test_a_call_appears_while_the_script_runs(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            block.update_call_msg(SCRIPT)
            block.append_live_output(self._frame(name="read", args='{"path":"a.py"}'))
            await pilot.pause()
            body = _body(block)
            assert "read" in body
            assert '{"path":"a.py"}' in body
            # Nothing has finished yet, so there is no duration to show.
            assert "0.0s" not in body

    @pytest.mark.asyncio
    async def test_an_in_flight_call_is_marked_as_running(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            block.update_call_msg(SCRIPT)
            block.append_live_output(self._frame(name="read", args='{"path":"a.py"}'))
            await pilot.pause()
            assert GLYPHS["running"] in _body(block)

    @pytest.mark.asyncio
    async def test_a_finished_call_is_not_shown_twice(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            block.update_call_msg(SCRIPT)
            block.append_live_output(self._frame(name="read", args='{"path":"a.py"}'))
            block.append_live_output(
                self._frame(name="read", status="ok", args='{"path":"a.py"}', duration_ms=120)
            )
            await pilot.pause()
            body = _body(block)
            # The host announces and then answers; two frames, one call.
            assert body.count("read") == 1
            assert GLYPHS["success"] in body
            assert "0.1s" in body

    @pytest.mark.asyncio
    async def test_two_identical_calls_in_a_row_are_two_rows(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            block.update_call_msg(SCRIPT)
            block.append_live_output(self._frame(name="read", args='{"path":"a.py"}'))
            block.append_live_output(
                self._frame(name="read", status="ok", args='{"path":"a.py"}', duration_ms=10)
            )
            # A second call with the same arguments is a distinct call, and the
            # script is the only thing that can tell them apart.
            block.append_live_output(self._frame(name="read", args='{"path":"a.py"}'))
            await pilot.pause()
            assert _body(block).count("a.py") == 2

    @pytest.mark.asyncio
    async def test_the_running_header_counts_finished_and_in_flight_calls(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            block.update_call_msg(SCRIPT)
            block.append_live_output(self._frame(name="read"))
            block.append_live_output(
                self._frame(name="grep", status="ok", args='{"p":1}', duration_ms=10)
            )
            block.append_live_output(self._frame(name="bash", args='{"c":"ls"}'))
            await pilot.pause()
            header = _header(block)
            assert "running" in header
            assert "1 call done" in header
            assert "2 in flight" in header

    @pytest.mark.asyncio
    async def test_the_finished_payload_replaces_the_live_ledger(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            block.update_call_msg(SCRIPT)
            block.append_live_output(self._frame(name="read", args='{"path":"a.py"}'))
            await pilot.pause()
            _finish(pilot, block, ui_data=_payload(value=None))
            await pilot.pause()
            body = _body(block)
            # The payload is the authoritative record; the ledger is only ever
            # the stand-in for it while there is nothing else.
            assert '{"path":"a.py"}' not in body
            assert '{"query":"find dead code"}' in body
            assert GLYPHS["running"] not in body


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
