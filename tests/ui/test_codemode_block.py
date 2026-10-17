"""The ``codemode`` tool block.

The point under test is that a codemode call reads as a *run* rather than as a
one-line receipt: the script, its nested calls, its output, and how long it all
took -- in that order, one block, no section furniture. Everything goes through
:meth:`ChatLog.set_tool_result`, which is the path the runner and session replay
both take, so the ``ui_data`` hand-off is covered by the same assertions.
"""

from __future__ import annotations

import json

import pytest
from textual.app import App, ComposeResult

from vtx.coding_agent.tools.codemode import PROGRESS_SENTINEL, CodemodeTool
from vtx.tui.blocks import ToolBlock
from vtx.tui.chat import ChatLog
from vtx.tui.codemode_block import BODY_INDENT, CodemodeToolBlock
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


def _call_row(body: str, name: str) -> str:
    """The one call row for ``name``, stripped.

    The script itself mentions the tool names too, so a row is found by its
    status glyph rather than by the name being absent from the rest of the block.
    """
    glyphs = (GLYPHS["success"], GLYPHS["failure"], GLYPHS["running"], GLYPHS["cancelled"])
    rows = [
        line.strip()
        for line in body.split("\n")
        if line.lstrip().startswith(glyphs) and line.split()[1] == name
    ]
    assert len(rows) == 1, f"expected one row for {name}, got {rows!r}"
    return rows[0]


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
        # A codemode call gets the whole-run treatment, but every other tool
        # keeps the plain receipt; this is what stops the registry growing a
        # custom block per tool.
        async with _TestApp().run_test() as pilot:
            chat = pilot.app.query_one("#chat-log", ChatLog)
            block = chat.start_tool("read", "r1", "some/file.py", icon="→")
            assert type(block) is ToolBlock


class TestShape:
    """The block has no section furniture; the blank lines are the only rule."""

    @pytest.mark.asyncio
    async def test_the_run_reads_top_to_bottom_with_no_labels(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(), summary="Script ok (3 tool calls)")
            await pilot.pause()
            body = _body(block)
            # `script` / `calls` / `output` / `value` restated what the shape
            # already says, four times over, on an eleven-line run.
            for label in ("⎿", "script", "calls", "output", "value"):
                assert label not in body
            # Order is what carries the meaning instead: what was written, what
            # it called, what came back, how long it took.
            assert body.index(FIRST_LINE) < body.index("search")
            assert body.index("search") < body.index("3 tools")
            assert body.index("3 tools") < body.index("Took")

    @pytest.mark.asyncio
    async def test_every_region_is_separated_by_one_blank_line(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(), summary="Script ok (3 tool calls)")
            await pilot.pause()
            lines = _body(block).split("\n")
            script_end = lines.index(f"{BODY_INDENT}return [r['name'] for r in rows]")
            assert lines[script_end + 1] == "", "the call list must not hug the script"
            assert lines[-1] == f"{BODY_INDENT}Took 1.2s"

    @pytest.mark.asyncio
    async def test_content_is_indented_like_every_other_tool_detail(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(), summary="Script ok (3 tool calls)")
            await pilot.pause()
            body = _body(block)
            assert body.startswith(f"{BODY_INDENT}{FIRST_LINE}")
            # Every non-blank line sits in the content column, including the
            # clock, which is otherwise a caption flush to the margin.
            for line in body.split("\n"):
                assert not line.strip() or line.startswith(BODY_INDENT), line


class TestOutput:
    """A printed line and a returned value are not the same claim about a run."""

    @pytest.mark.asyncio
    async def test_the_returned_value_is_the_last_output_item(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(value=None, printed=["3 tools", "done"]))
            await pilot.pause()
            body = _body(block)
            assert "3 tools" in body
            assert "done" in body
            # A script that only prints is not returning the printed lines, and
            # the block must not read as though it were.
            assert "value" not in body

    @pytest.mark.asyncio
    async def test_more_than_one_item_is_labelled_so_they_can_be_told_apart(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload())
            await pilot.pause()
            body = _body(block)
            assert "==> text 1/2 <==" in body
            assert "==> text 2/2 <==" in body
            # The value is the last item, so it is the answer the reader wants.
            assert body.index("==> text 2/2 <==") < body.index('["edit", "grep", "read"]')

    @pytest.mark.asyncio
    async def test_a_single_item_is_not_labelled(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(value=None, printed=["3 tools"]))
            await pilot.pause()
            # With one item the label restates the only fact already on screen.
            assert "==>" not in _body(block)

    @pytest.mark.asyncio
    async def test_output_keeps_its_tail_so_the_value_survives_the_preview(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(
                pilot,
                block,
                ui_data=_payload(value="ANSWER", printed=[f"finding {i}" for i in range(30)]),
            )
            await pilot.pause()
            body = _body(block)
            # The returned value is the last item; previewing the head would
            # throw away the one thing the script produced an answer about.
            assert "finding 0" not in body
            assert "ANSWER" in body
            assert "ctrl+o to expand" in body

    @pytest.mark.asyncio
    async def test_a_preview_never_splits_an_item_from_its_label(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(
                pilot,
                block,
                ui_data=_payload(value="ANSWER", printed=[f"finding {i}" for i in range(6)]),
            )
            await pilot.pause()
            lines = _body(block).split("\n")
            # Every label in view is immediately followed by the text it names.
            for index, line in enumerate(lines[:-1]):
                if line.strip().startswith("==> text"):
                    assert lines[index + 1].strip(), "a label with nothing under it"

    @pytest.mark.asyncio
    async def test_output_is_withheld_until_the_run_ends(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            block.update_call_msg(SCRIPT)
            await pilot.pause()
            assert "3 tools" not in _body(block)


class TestCalls:
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
    async def test_a_row_is_glyph_name_args_duration(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload())
            await pilot.pause()
            row = _call_row(_body(block), "search")
            assert row == '✓ search {"query":"find dead code"} 412ms'

    @pytest.mark.asyncio
    async def test_durations_keep_their_own_precision(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload())
            await pilot.pause()
            body = _body(block)
            # A 90ms read shown as `0.1s` says nothing; `412ms` says it all.
            assert "90ms" in body
            assert "412ms" in body
            assert "0.0s" not in body

    @pytest.mark.asyncio
    async def test_a_failed_call_says_why(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload())
            await pilot.pause()
            body = _body(block)
            assert f"{GLYPHS['failure']} bash" in body
            # A bare ✗ cannot be told from a denial, a missing file and a bad
            # argument, so the reason stays even on a collapsed row.
            assert "exit 1 · tool_failure" in body

    @pytest.mark.asyncio
    async def test_an_in_flight_call_shows_no_duration(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            block.update_call_msg(SCRIPT)
            block.append_live_output(
                f"{PROGRESS_SENTINEL}"
                + json.dumps(
                    {
                        "name": "read",
                        "status": "running",
                        "args": '{"path":"a"}',
                        "kind": None,
                        "message": None,
                        "duration_ms": None,
                    }
                )
                + "\n"
            )
            await pilot.pause()
            row = _call_row(_body(block), "read")
            assert row.strip() == f'{GLYPHS["running"]} read {{"path":"a"}}'
            assert "ms" not in row

    @pytest.mark.asyncio
    async def test_a_cancelled_call_has_its_own_glyph(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(
                pilot, block, ui_data=_payload(calls=[_call("read", ms=None, status="cancelled")])
            )
            await pilot.pause()
            assert GLYPHS["cancelled"] in _body(block)

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
    async def test_short_arguments_are_still_drawn(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(calls=[_call("read", ms=5, args='{"p":"a"}')]))
            await pilot.pause()
            # `{"p":"a"}` is short, not uninteresting. Refusing to draw it
            # because it is small throws away the only thing that distinguishes
            # two calls of the same tool.
            assert '{"p":"a"}' in _body(block)

    @pytest.mark.asyncio
    async def test_a_long_argument_is_elided_rather_than_wrapped(self):
        blob = '{"pattern":"' + "x" * 300 + '"}'
        async with _TestApp().run_test(size=(90, 60)) as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(calls=[_call("grep", ms=5, args=blob)]))
            await pilot.pause()
            row = _call_row(_body(block), "grep")
            # Cut in the middle and marked, rather than wrapped onto a second
            # line the eye then has to reassemble against the row above it.
            assert "..." in row
            assert "5ms" in row
            assert len(row) < len(blob)


class TestPreviews:
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
    async def test_the_script_preview_keeps_its_head(self):
        long_script = "\n".join(f"x{i} = {i}" for i in range(40))
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool(), code=long_script)
            _finish(pilot, block, ui_data=_payload(code=long_script))
            await pilot.pause()
            body = _body(block)
            # A script's head is what it came to do; its tail is elaboration.
            assert "x0 = 0" in body
            assert "x39 = 39" not in body


class TestFailure:
    @pytest.mark.asyncio
    async def test_the_failure_keeps_its_cause_and_whole_traceback(self):
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
            assert "script · NameError" in body
            assert "Traceback" in body

    @pytest.mark.asyncio
    async def test_the_whole_traceback_is_shown(self):
        stack = "\n".join(
            f'  File "<codemode>", line {i}, in __codemode_main__' for i in range(30)
        )
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
            # A traceback cut off mid-frame is not a traceback, so this one is
            # never previewed however long it gets.
            assert "ctrl+o to expand" not in body


class TestTimer:
    @pytest.mark.asyncio
    async def test_the_clock_runs_while_the_script_does(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            block.update_call_msg(SCRIPT)
            await pilot.pause()
            assert "Elapsed" in _body(block)

    @pytest.mark.asyncio
    async def test_the_elapsed_clock_is_replaced_by_a_taken_one(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            block.update_call_msg(SCRIPT)
            await pilot.pause()
            _finish(pilot, block, ui_data=_payload(), summary="Script ok (3 tool calls)")
            await pilot.pause()
            body = _body(block)
            assert "Took 1.2s" in body
            assert "Elapsed" not in body

    @pytest.mark.asyncio
    async def test_the_clock_escalates_past_a_minute(self):
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(duration_ms=90_000))
            await pilot.pause()
            assert "Took 1m 30s" in _body(block)


class TestLiveCalls:
    """A run that says nothing for ninety seconds is indistinguishable from hung."""

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
            assert "120ms" in body

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


class TestNarrowTerminals:
    """A block that wraps correctly is the one that stays readable."""

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
                    printed=[long_out],
                    calls=[_call("read", ms=5, args='{"path":"' + long_word + '"}')],
                ),
            )
            await pilot.pause()
            for line in _body(block).split("\n"):
                assert len(line) <= 70, f"line runs past the block: {line[:90]!r}"

    @pytest.mark.asyncio
    async def test_a_wrapped_line_keeps_its_indent(self):
        out = "scanned a great many files and found rather a lot of them indeed"
        async with _TestApp().run_test(size=(60, 60)) as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, ui_data=_payload(value=None, printed=[out]))
            await pilot.pause()
            wrapped = [
                line
                for line in _body(block).split("\n")
                if line.strip() and not line.startswith(BODY_INDENT)
            ]
            assert not wrapped, f"a continuation escaped the indent: {wrapped[:2]!r}"


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


class TestFallback:
    @pytest.mark.asyncio
    async def test_a_session_without_a_payload_still_shows_the_script(self):
        # Sessions written before the payload existed carry no ui_data. The body
        # still renders the block from the call text.
        async with _TestApp().run_test() as pilot:
            block = await _start(pilot, CodemodeTool())
            _finish(pilot, block, success=False, ui_data=None, summary="failed")
            await pilot.pause()
            assert FIRST_LINE in _body(block)
