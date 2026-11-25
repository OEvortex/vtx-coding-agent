"""Thinking block geometry, and the width contract every block keeps."""

from typing import ClassVar

from vx.tui import ansi, blocks


def rows(block: blocks.Block, width: int = 80) -> list[str]:
    return [ansi.strip_ansi(row) for row in block.rows(width)]


def pin_budget(monkeypatch, value: int | None) -> None:
    monkeypatch.setattr(blocks, "thinking_line_budget", lambda: value)


class TestThinking:
    def test_live_thought_streams_under_a_spine(self, monkeypatch):
        pin_budget(monkeypatch, 2)
        block = blocks.Thinking(text="one\ntwo\nthree", streaming=True)
        out = rows(block)
        assert out[0] == "● Thinking"  # no duration until it finishes
        assert all(row.startswith("│ ") for row in out[1:])
        assert len(out) == 4  # every row, folding is for finished thoughts only

    def test_finished_thought_reports_its_duration(self, monkeypatch):
        pin_budget(monkeypatch, 5)
        out = rows(blocks.Thinking(text="short", streaming=False, elapsed=4.0))
        assert out[0] == "● Thinking · 4.0s"

    def test_folded_rows_use_the_tree_and_count_the_rest(self, monkeypatch):
        pin_budget(monkeypatch, 1)
        out = rows(blocks.Thinking(text="one\ntwo\nthree", streaming=False, elapsed=1.0))
        assert out[0] == "● Thinking · 1.0s"
        assert out[1].startswith("├ ")
        assert out[2] == "└ ... (2 more rows)"

    def test_everything_shown_uses_fork_then_last(self, monkeypatch):
        pin_budget(monkeypatch, 50)
        out = rows(blocks.Thinking(text="one\ntwo\nthree", streaming=False, elapsed=1.0))
        assert out[1].startswith("├ ")
        assert out[2].startswith("├ ")
        assert out[3].startswith("└ ")
        assert not any("more row" in row for row in out)

    def test_empty_thought_is_only_the_header(self, monkeypatch):
        pin_budget(monkeypatch, 2)
        assert rows(blocks.Thinking(text="", streaming=False, elapsed=0.5)) == [
            "● Thinking · 0.5s"
        ]


class TestWidthContract:
    """No block may draw a row wider than the width it was given.

    One extra cell wraps the terminal, shifts every row below it and leaves the
    composer sitting on the divider, so this is a hard rule rather than taste.
    """

    DIFF: ClassVar[list[tuple[str, str, str]]] = [
        (" ", "83", "    def _rebuild(self, width: int) -> None:"),
        ("-", "85", "        self._rows = rows"),
        ("+", "85", "        if self.scroll > 0:"),
        ("+", "87", "            self.scroll = max(0, self.scroll + len(rows) - len(self._rows))"),
        (" ", "89", "        self._width = width"),
    ]

    def test_diff_rows_fit_every_width(self):
        for width in range(40, 160):
            block = blocks.DiffOutput(lines=self.DIFF, path="a/b/viewport.py")
            for row in block.rows(width):
                assert ansi.width(row) <= width, (width, ansi.strip_ansi(row))

    def test_diff_wraps_instead_of_clipping_the_line(self):
        rows = [ansi.strip_ansi(r) for r in blocks.DiffOutput(lines=self.DIFF).rows(80)]
        joined = "".join(rows)
        assert "self.scroll + len(rows) - len(self._rows)" not in joined  # it wrapped
        assert "self._max_scroll" not in joined
        assert any("len(self._rows))" in row for row in rows)

    def test_a_long_command_elides_to_one_row(self):
        command = "rg -n 'self.scroll' packages/vx/src/vx | head -40"
        rows = blocks.ShellRun(command=command, output="", done=False).rows(40)
        assert len(rows) == 1
        assert ansi.width(rows[0]) <= 40

    def test_narrow_notice_does_not_take_the_whole_word_as_a_char(self):
        # A word holding an escape sequence used to be measured as characters.
        note = blocks.Notice(topic="provider", body="invalid key in **~/.ctx/config.yml**")
        for width in range(20, 60):
            for row in note.rows(width):
                assert ansi.width(row) <= width, (width, ansi.strip_ansi(row))


class TestSpacing:
    """Blocks are separated by one blank row, tool groups included.

    A tool group welded to the reply above it reads as part of that sentence,
    and two groups in a row read as one; a blank row is what keeps each block
    its own thought.
    """

    def test_every_kind_gets_a_gap_after_the_first(self):
        kinds = ("welcome", "user", "thinking", "assistant", "tool", "notice", "usage")
        for kind in kinds:
            assert blocks.spacing("assistant", kind) == 1
            assert blocks.spacing("tool", kind) == 1

    def test_nothing_precedes_the_first_block(self):
        assert blocks.spacing(None, "tool") == 0

    def test_a_tool_group_is_flanked_by_blank_rows(self):
        from vx.tui.viewport import Viewport

        class Stub(blocks.Block):
            kind = "tool"

            def __init__(self, kind: str, lines: list[str]) -> None:
                self.kind = kind
                self._lines = lines

            def rows(self, width: int) -> list[str]:
                return self._lines

        viewport = Viewport()
        viewport.add(Stub("assistant", ["reply"]))
        viewport.add(Stub("tool", ["● Ran ls"]))
        viewport.add(Stub("notice", ["done"]))
        rows = [ansi.strip_ansi(row) for row in viewport.lines(40)]
        assert rows == ["reply", "", "● Ran ls", "", "done"]

    def test_a_patch_diff_stays_welded_under_its_row(self):
        from vx.tui.viewport import Viewport

        viewport = Viewport()
        viewport.add(blocks.ApplyPatchToolStatus(path="a.py", added=1, status="ok"))
        viewport.add(blocks.DiffOutput(lines=[("+", "1", "x = 1")], path="a.py"))
        rows = [ansi.strip_ansi(row) for row in viewport.lines(80)]
        assert rows[0].startswith("● Edited a.py")
        assert rows[1].startswith("  ± a.py")  # no blank row between them


class TestTurnSummary:
    def test_the_recap_carries_no_usage(self):
        block = blocks.TurnSummary(recap="Ran 1 command · edited 1 file", seconds=10.0)
        assert rows(block) == ["  Ran 1 command · edited 1 file · 10s"]

    def test_a_recap_with_no_measured_duration_still_reads(self):
        assert rows(blocks.TurnSummary(recap="Explored 2 files")) == ["  Explored 2 files · 0s"]
