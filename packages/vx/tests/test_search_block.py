"""The search row and its detail block: what they say, and that they fit."""

import pytest
from vx import app
from vx.tui import ansi, blocks


def plain(row: str) -> str:
    return ansi.strip_ansi(row).rstrip()


def row(
    *,
    pattern: str = "needle",
    mode: str = "content",
    status: str = "ok",
    scope: str = "",
    glob: str = "",
    backend: str = "",
    matches: int = 0,
    files: int = 0,
    total: int = 0,
    scanned: int = 0,
    error: str = "",
    elapsed: float | None = 0.025,
) -> blocks.SearchToolStatus:
    return blocks.SearchToolStatus(
        pattern=pattern,
        mode=mode,
        status=status,
        scope=scope,
        glob=glob,
        backend=backend,
        matches=matches,
        files=files,
        total=total,
        scanned=scanned,
        error=error,
        elapsed=elapsed,
    )


class TestRow:
    def test_a_content_search_reads_like_a_sentence(self):
        found = row(matches=3, files=2)
        assert plain(found.rows(100)[0]) == "● Searched needle · 3 matches in 2 files · 25ms ✓"

    def test_one_match_is_singular(self):
        assert "1 match in 1 file" in plain(row(matches=1, files=1).rows(100)[0])

    def test_running_says_searching(self):
        line = plain(row(status="running", elapsed=None).rows(100)[0])
        assert line.startswith("● Searching needle")
        assert "✓" not in line

    def test_files_mode_reads_found(self):
        found = row(mode="files", pattern="*.py", files=6)
        assert plain(found.rows(100)[0]) == "● Found *.py · 6 files · 25ms ✓"

    def test_files_mode_counts_what_it_showed(self):
        found = row(mode="files", pattern="*.py", files=4, total=32)
        assert "4 of 32 files" in plain(found.rows(100)[0])

    def test_nothing_found_says_what_it_looked_at(self):
        found = row(matches=0, files=0, scanned=537)
        assert plain(found.rows(100)[0]) == "● Searched needle · no matches in 537 files · 25ms ✓"

    def test_a_fallback_backend_names_itself(self):
        found = row(matches=3, files=2, backend="git grep")
        assert "· git grep" in plain(found.rows(100)[0])

    def test_the_first_choice_backend_stays_quiet(self):
        assert "rg" not in plain(row(matches=3, files=2, backend="rg").rows(100)[0])
        assert "fd" not in plain(row(mode="files", files=2, backend="fd").rows(100)[0])

    def test_a_scope_is_shown_when_it_is_not_the_cwd(self):
        found = row(matches=1, files=1, scope="packages/vx/src")
        assert "· packages/vx/src" in plain(found.rows(100)[0])

    def test_a_refusal_is_a_sentence_not_a_count(self):
        found = row(status="error", error="refusing to search / · give a path or a glob")
        line = plain(found.rows(100)[0])
        assert line.startswith("● Couldn't search · refusing to search /")
        assert "✓" not in line

    def test_milliseconds_not_tenths(self):
        assert "25ms" in plain(row(elapsed=0.025).rows(100)[0])
        assert "1.5s" in plain(row(elapsed=1.5).rows(100)[0])

    def test_a_long_pattern_is_clipped_not_wrapped(self):
        long = row(pattern="x" * 200, matches=1, files=1)
        assert len(long.rows(100)) == 1
        assert ansi.width(long.rows(100)[0]) <= 100

    def test_category_follows_the_mode(self):
        assert row().category == "read"
        assert row(mode="files").category == "list"

    def test_files_mode_joins_a_group_as_a_listing(self):
        group = blocks.ToolGroup(calls=[row(mode="files", files=2), row(matches=2, files=1)])
        header = plain(group.rows(100)[0])
        assert "listed" in header.lower()


class TestResults:
    """The only thing drawn under a search row: which patterns hit."""

    def detail(self, counts: tuple[tuple[str, int], ...]) -> blocks.SearchResults:
        return blocks.SearchResults(counts=counts)

    def test_a_search_draws_no_hits(self):
        # A preview is a pager; the row already says how many were found.
        assert self.detail(()).rows(100) == []
        assert self.detail((("needle", 3),)).rows(100) == []

    def test_the_zero_is_kept(self):
        rows = [plain(r) for r in self.detail((("needle", 3), ("absent", 0))).rows(100)]
        assert rows == ["  needle 3 · absent 0"]

    @pytest.mark.parametrize("width", [20, 40, 60, 80, 100, 140, 200])
    def test_every_row_fits_every_width(self, width: int):
        detail = self.detail((("needle", 3), ("a-very-long-alternative-name", 0)))
        for line in detail.rows(width):
            assert ansi.width(line) <= width

    def test_a_single_pattern_draws_nothing_until_asked(self):
        block = app._search_results(
            {"counts": [["needle", 3]], "rows": [{"path": "a.py", "line": 1, "text": "x"}]}
        )
        assert block is not None  # placed, so ctrl+o has something to open
        assert block.rows(80) == []

    def test_the_counts_line_is_always_drawn(self):
        block = app._search_results({"counts": [["needle", 3], ["absent", 0]]})
        assert block is not None
        assert [plain(r) for r in block.rows(80)] == ["  needle 3 · absent 0"]

    def test_hits_come_back_only_when_expanded(self):
        ui = {
            "rows": [
                {"path": "a.py", "line": 3, "text": "needle = 1"},
                {"path": "a.py", "line": 12, "text": "needle = 2"},
            ],
            "total": 2,
        }
        block = app._search_results(ui)
        assert block is not None
        assert block.rows(80) == []
        blocks.set_expanded(True)
        try:
            assert [plain(r) for r in block.rows(80)] == [
                "  a.py",
                "   3 │ needle = 1",
                "  12 │ needle = 2",
            ]
        finally:
            blocks.set_expanded(False)


class TestGrouping:
    def test_a_search_joins_the_reads_the_group_already_names(self):
        group = blocks.ToolGroup(calls=[row(matches=2, files=1), blocks.ToolStatus(name="read")])
        assert "read files" in plain(group.rows(100)[0]).lower()

    def test_several_calls_keep_one_row_each(self):
        group = blocks.ToolGroup(
            calls=[row(pattern="a", matches=1, files=1), row(pattern="b", matches=0, scanned=10)]
        )
        rows = [plain(r) for r in group.rows(100)]
        assert len(rows) == 3
        assert rows[1].startswith("├ Searched a")
        assert rows[2].startswith("└ Searched b")


class TestFailedRow:
    """A failure has to say what failed; `no results` is not a reason."""

    class _Text:
        def __init__(self, text: str) -> None:
            self.text = text

    class _Result:
        is_error = True
        ui_data = None

        def __init__(self, text: str) -> None:
            self.content = [TestFailedRow._Text(text)]

    def test_a_harness_error_is_not_called_no_results(self):
        block = blocks.SearchToolStatus(name="search", pattern="needle")
        block.status = "error"
        assert "no results" in blocks._tool_row(block, 120, "")

        app._apply_search(block, self._Result("Expecting ',' delimiter: line 1 column 108"))
        drawn = blocks._tool_row(block, 120, "")
        assert "no results" not in drawn
        assert "delimiter" in drawn


class TestNoMatchRow:
    def test_a_file_scope_says_no_matches_plainly(self):
        drawn = plain(blocks._tool_row(row(matches=0, files=0, scanned=0), 100, ""))
        assert "no matches" in drawn
        assert "0 files" not in drawn

    def test_a_tree_scope_says_how_many_files_it_looked_in(self):
        drawn = plain(blocks._tool_row(row(matches=0, files=0, scanned=44), 100, ""))
        assert "no matches in 44 files" in drawn
