"""A patch that is still arriving: rows, caret, and the handover to the tool."""

import asyncio
from typing import cast

from ctx.core.events import ToolArgsDeltaEvent, ToolResultEvent, ToolStartEvent
from ctx.protocol.types import TextContent, ToolResultMessage
from vx.app import VxApp, _stream_patch_sections
from vx.tui import ansi, blocks
from vx.tui.terminal import Terminal


class StubTerminal:
    _fd = -1

    def size(self) -> tuple[int, int]:
        return 24, 100

    def write(self, data: str) -> None:
        pass

    def enter(self) -> None:
        pass

    def exit(self) -> None:
        pass


def make_app() -> VxApp:
    app = VxApp(cwd="/tmp", terminal=cast(Terminal, StubTerminal()))
    app._ready = asyncio.Event()
    app._ready.set()
    app._draw = lambda *args, **kwargs: None
    return app


def rows_of(args: dict) -> list[tuple[str, str, str]]:
    sections = _stream_patch_sections(args)
    assert sections, args
    return sections[0][1]


def test_a_replacement_reads_as_removed_then_added():
    (path, rows, added, removed) = _stream_patch_sections(
        {"ops": [{"path": "a.py", "old_str": "x = 1\n", "new_str": "x = 2\ny = 3\n"}]}
    )[0]
    assert path == "a.py"
    assert rows == [("-", "", "x = 1"), ("+", "", "x = 2"), ("+", "", "y = 3")]
    assert (added, removed) == (2, 1)


def test_a_new_file_is_all_added():
    rows = rows_of({"ops": [{"path": "b.py", "new_file_content": "one\ntwo\n"}]})
    assert rows == [("+", "", "one"), ("+", "", "two")]


def test_the_line_still_being_written_is_kept():
    # No trailing newline: that line has not finished arriving.
    rows = rows_of({"ops": [{"path": "a.py", "new_str": "x = 1\nx = 2"}]})
    assert rows[-1] == ("+", "", "x = 2")


def test_rows_carry_no_line_numbers_yet():
    # The arguments are a search/replace patch, so the text is known and the
    # place in the file is not.
    rows = rows_of({"ops": [{"path": "a.py", "old_str": "a\n", "new_str": "b\n"}]})
    assert all(line_number == "" for _op, line_number, _text in rows)


def test_args_with_no_text_yet_draw_nothing():
    assert _stream_patch_sections({"ops": [{"path": "a.py"}]}) == []
    assert _stream_patch_sections({}) == []


def test_only_the_first_files_show_while_streaming():
    ops = [{"path": f"f{index}.py", "new_str": "x\n"} for index in range(6)]
    sections = _stream_patch_sections({"ops": ops})
    assert [path for path, *_rest in sections] == ["f0.py", "f1.py", "f2.py"]


def test_a_huge_stream_stops_at_the_row_cap():
    sections = _stream_patch_sections(
        {"ops": [{"path": "big.py", "new_file_content": "line\n" * 400}]}
    )
    (_path, rows, added, _removed) = sections[0]
    assert len(rows) == 120
    # The counts describe the whole op, not the part on screen.
    assert added == 400


def test_the_caret_marks_the_open_line():
    block = blocks.DiffOutput(lines=[("+", "", "x = 1")], streaming=True)
    assert "▏" in ansi.strip_ansi(block.rows(80)[-1])


def test_no_caret_at_the_start_of_a_line():
    # A bare caret under a `+` reads as an empty hunk line.
    block = blocks.DiffOutput(lines=[("+", "", "x = 1"), ("+", "", "")], streaming=True)
    text = "\n".join(ansi.strip_ansi(row) for row in block.rows(80))
    assert "▏" not in text


def test_a_landed_diff_has_no_caret():
    block = blocks.DiffOutput(lines=[("+", "", "x = 1")])
    assert "▏" not in ansi.strip_ansi(block.rows(80)[-1])


def test_deltas_draw_a_growing_diff_and_the_result_replaces_it():
    app = make_app()
    app._handle_event(ToolStartEvent(tool_call_id="call-1", tool_name="apply_patch"))
    delta = ToolArgsDeltaEvent(
        tool_call_id="call-1",
        delta="",
        partial_arguments={"ops": [{"path": "a.py", "new_str": "one\ntwo\n"}]},
    )
    app._handle_event(delta)
    block = app._tool_blocks["call-1"]
    assert isinstance(block, blocks.ApplyPatchToolStatus)
    assert block.path == "a.py"
    assert (block.added, block.removed) == (2, 0)
    diff = app._stream_diffs["call-1"]
    assert diff.streaming
    assert [row[2] for row in diff.lines] == ["one", "two"]
    assert diff in app.viewport.blocks

    longer = ToolArgsDeltaEvent(
        tool_call_id="call-1",
        delta="",
        partial_arguments={"ops": [{"path": "a.py", "new_str": "one\ntwo\nthree\n"}]},
    )
    app._handle_event(longer)
    assert app._stream_diffs["call-1"] is diff
    assert [row[2] for row in diff.lines] == ["one", "two", "three"]

    done = ToolResultEvent(
        tool_call_id="call-1",
        tool_name="apply_patch",
        result=ToolResultMessage(
            tool_call_id="call-1",
            tool_name="apply_patch",
            content=[TextContent(text="Edited a.py")],
            ui_data={
                "diff_groups": [{"path": "a.py", "lines": [["+", "1", "one"], ["+", "2", "two"]]}]
            },
        ),
    )
    app._handle_event(done)
    assert "call-1" not in app._stream_diffs
    # The preview is gone and the tool's own diff, with real line numbers, is in.
    assert diff not in app.viewport.blocks
    rendered = [ansi.strip_ansi(row) for block_ in app.viewport.blocks for row in block_.rows(100)]
    assert any("1 │+ one" in row for row in rendered)
