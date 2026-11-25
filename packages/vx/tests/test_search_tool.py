"""The search tool: the ladder, the caps, and the traps that make it wrong."""

import asyncio
import subprocess
from pathlib import Path

import pytest
from vx.tools import search
from vx.tools.search import SearchParams, SearchTool

TOOL = SearchTool()


@pytest.fixture(autouse=True)
def _fresh_git_cache():
    search._GIT_ROOTS.clear()
    yield
    search._GIT_ROOTS.clear()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A small project with the directories a naive walk would get wrong."""
    (tmp_path / "alpha.py").write_text("needle here\nalpha\n")
    (tmp_path / "beta.md").write_text("needle too\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "gamma.py").write_text("def needle_fn():\n    pass\n")
    for junk in (".venv", "node_modules", "__pycache__"):
        (tmp_path / junk).mkdir()
        (tmp_path / junk / "skip.py").write_text("needle hidden\n")
    return tmp_path


def hide(*names: str):
    """Take binaries away the way a bare machine has them away."""
    real = search._which
    return lambda name: None if name in names else real(name)


def find(**kwargs):
    return asyncio.run(TOOL.execute(SearchParams(**kwargs)))


def paths(result) -> list[str]:
    return [row["path"] for row in result.ui_data["rows"]]


# =================================================================================================
# The ladder
# =================================================================================================


class TestLadder:
    def test_ripgrep_is_the_first_rung(self, tree: Path):
        if search._which("rg") is None:
            pytest.skip("rg not installed")
        result = find(pattern="needle", path=str(tree))
        assert result.success
        assert result.ui_data["backend"] == "rg"

    def test_git_grep_when_ripgrep_is_missing(self, tree: Path, monkeypatch):
        monkeypatch.setattr(search, "_which", hide("rg"))
        result = find(pattern="needle", path=str(tree))
        assert result.success
        # no git repo here, so the Python rung answers
        assert result.ui_data["backend"] in ("git files", "python")
        assert len(result.ui_data["rows"]) == 3

    def test_python_floor_with_no_tools_at_all(self, tree: Path, monkeypatch):
        monkeypatch.setattr(search, "_which", lambda name: None)
        result = find(pattern="needle", path=str(tree))
        assert result.success
        assert result.ui_data["backend"] == "python"
        assert len(result.ui_data["rows"]) == 3

    def test_the_floor_skips_build_and_vendored_dirs(self, tree: Path, monkeypatch):
        monkeypatch.setattr(search, "_which", lambda name: None)
        result = find(pattern="needle", path=str(tree))
        assert not any("skip.py" in path for path in paths(result))
        assert len(result.ui_data["rows"]) == 3

    def test_ignored_dirs_come_back_with_no_ignore(self, tree: Path, monkeypatch):
        monkeypatch.setattr(search, "_which", lambda name: None)
        result = find(pattern="needle", path=str(tree), no_ignore=True)
        assert any("skip.py" in path for path in paths(result))

    def test_files_mode_uses_fd(self, tree: Path):
        if search._which("fd") is None:
            pytest.skip("fd not installed")
        result = find(mode="files", pattern="*.py", path=str(tree))
        assert result.success
        assert result.ui_data["backend"] == "fd"
        assert len(result.ui_data["files"]) == 2

    def test_files_mode_falls_back_to_python(self, tree: Path, monkeypatch):
        monkeypatch.setattr(search, "_which", lambda name: None)
        result = find(mode="files", pattern="*.py", path=str(tree))
        assert result.success
        assert sorted(result.ui_data["files"]) == [
            str(tree / "alpha.py"),
            str(tree / "sub" / "gamma.py"),
        ]

    def test_files_mode_takes_a_bare_name(self, tree: Path):
        result = find(mode="files", pattern="gamma", path=str(tree))
        assert [Path(path).name for path in result.ui_data["files"]] == ["gamma.py"]


class TestGitRung:
    """`git grep` is the rung where a flag is the difference between right and silent."""

    @pytest.fixture
    def repo(self, tree: Path) -> Path:
        if search._which("git") is None:
            pytest.skip("git not installed")
        subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
        subprocess.run(["git", "add", "alpha.py"], cwd=tree, check=True)
        return tree

    def test_alternation_needs_extended_regex(self, repo: Path, monkeypatch):
        # BRE reads `|` as a literal: the wrong answer, silently, and fast.
        monkeypatch.setattr(search, "_which", hide("rg"))
        result = find(pattern="needle|alpha", path=str(repo))
        assert result.success
        assert result.ui_data["backend"] == "git grep"
        assert len(result.ui_data["rows"]) >= 3

    def test_untracked_files_are_searched(self, repo: Path, monkeypatch):
        (repo / "fresh.py").write_text("needle in a brand new file\n")
        monkeypatch.setattr(search, "_which", hide("rg"))
        result = find(pattern="brand new", path=str(repo))
        assert result.ui_data["backend"] == "git grep"
        assert any("fresh.py" in path for path in paths(result))

    def test_untracked_file_found_by_the_floor_too(self, repo: Path, monkeypatch):
        (repo / "fresh.py").write_text("needle in a brand new file\n")
        monkeypatch.setattr(search, "_which", lambda name: None)
        result = find(pattern="brand new", path=str(repo))
        assert any("fresh.py" in path for path in paths(result))


# =================================================================================================
# Shape of the answer
# =================================================================================================


class TestAnswers:
    def test_empty_result_counts_what_it_looked_at(self, tree: Path):
        result = find(pattern="zzznotpresent", path=str(tree))
        assert result.success
        assert result.ui_data["scanned"] == 3
        assert "No matches" in result.result
        assert "in 3 files" in result.result

    def test_empty_result_mentions_the_ignore_hint(self, tree: Path):
        assert "no_ignore" in find(pattern="zzznotpresent", path=str(tree)).result
        quiet = find(pattern="zzznotpresent", path=str(tree), no_ignore=True)
        assert "no_ignore" not in quiet.result

    def test_hits_are_path_line_text(self, tree: Path):
        result = find(pattern="needle_fn", path=str(tree))
        assert result.result.splitlines()[1].endswith("sub/gamma.py:1:def needle_fn():")

    def test_glob_narrows_content(self, tree: Path):
        result = find(pattern="needle", path=str(tree), glob="*.md")
        assert paths(result) == [str(tree / "beta.md")]

    def test_case_sensitivity(self, tree: Path):
        (tree / "upper.py").write_text("Needle\n")
        loose = find(pattern="needle", path=str(tree))
        strict = find(pattern="needle", path=str(tree), case_sensitive=True)
        assert len(strict.ui_data["rows"]) == len(loose.ui_data["rows"]) - 1

    def test_literal_mode_does_not_read_regex(self, tree: Path):
        assert len(find(pattern="ne..le", path=str(tree)).ui_data["rows"]) == 3
        assert find(pattern="ne..le", path=str(tree), literal=True).ui_data["rows"] == []

    def test_binary_files_are_skipped(self, tree: Path):
        (tree / "blob.bin").write_bytes(b"\x00\x01needle\x00")
        result = find(pattern="needle", path=str(tree))
        assert not any("blob.bin" in path for path in paths(result))

    def test_a_single_file_is_a_scope(self, tree: Path):
        result = find(pattern="needle", path=str(tree / "alpha.py"))
        assert paths(result) == [str(tree / "alpha.py")]


class TestCaps:
    def test_max_results_caps_the_rows_and_says_so(self, tree: Path):
        result = find(pattern="needle", path=str(tree), max_results=2)
        assert len(result.ui_data["rows"]) == 2
        assert result.ui_data["truncated"] is True
        assert result.ui_data["total"] == 3
        assert "showing 2 of 3" in result.result

    def test_max_results_is_capped_at_the_hard_limit(self):
        assert SearchParams(pattern="x", max_results=10_000).limit() == search.MAX_RESULTS
        assert SearchParams(pattern="x", max_results=0).limit() == search.DEFAULT_RESULTS

    def test_files_mode_caps_and_counts(self, tree: Path):
        result = find(mode="files", pattern="*.py", path=str(tree), max_results=1)
        assert len(result.ui_data["files"]) == 1
        assert result.ui_data["total"] == 2


class TestAlternatives:
    def test_counts_every_alternative_including_the_zero(self, tree: Path, monkeypatch):
        monkeypatch.setattr(search, "_which", lambda name: None)
        result = find(pattern="needle|absent", path=str(tree))
        counts = dict(result.ui_data["counts"])
        assert counts["absent"] == 0
        assert counts["needle"] == 3

    def test_the_counts_line_leads_the_answer(self, tree: Path, monkeypatch):
        monkeypatch.setattr(search, "_which", lambda name: None)
        result = find(pattern="needle|absent", path=str(tree))
        assert result.result.splitlines()[1] == "needle 3 · absent 0"

    def test_one_pattern_has_no_counts(self, tree: Path):
        assert find(pattern="needle", path=str(tree)).ui_data["counts"] == []

    def test_alternatives_inside_a_group_are_not_split(self):
        assert SearchParams(pattern="(alpha|beta)c").alternatives() == []

    def test_alternatives_parser(self):
        assert SearchParams(pattern="a|b|c").alternatives() == ["a", "b", "c"]
        assert SearchParams(pattern=r"a\|b").alternatives() == []
        assert SearchParams(pattern="a|").alternatives() == []
        assert SearchParams(pattern="a[|]b").alternatives() == []

    def test_patterns_are_escaped_and_joined(self):
        params = SearchParams(pattern=["a.c", "b(c"], literal=True)
        assert params.query() == r"a\.c|b\(c"

    def test_patterns_find_literals(self, tree: Path, monkeypatch):
        monkeypatch.setattr(search, "_which", lambda name: None)
        result = find(pattern=["needle_fn", "needle too"], literal=True, path=str(tree))
        assert len(result.ui_data["rows"]) == 2


class TestRefusals:
    def test_the_root_is_refused(self):
        result = find(pattern="hint_row", path="/")
        assert result.success is False
        assert "refusing to search /" in result.result

    def test_a_glob_makes_a_wide_root_acceptable(self):
        assert search._refusal(Path("/"), SearchParams(pattern="x", glob="*.py")) == ""
        assert search._refusal(Path("/"), SearchParams(mode="files", pattern="*.py")) == ""
        assert search._refusal(Path("/"), SearchParams(pattern="x", path="/")) != ""

    def test_a_missing_path_is_an_error(self):
        result = find(pattern="x", path="no/such/place")
        assert result.success is False
        assert "no such path" in result.result

    def test_no_pattern_is_an_error(self):
        result = find()
        assert result.success is False
        assert "give a pattern" in result.result

    def test_a_bad_regex_is_an_error(self, tree: Path, monkeypatch):
        monkeypatch.setattr(search, "_which", lambda name: None)
        result = find(pattern="a(b", path=str(tree))
        assert result.success is False
        assert "bad pattern" in result.result


class TestParameters:
    def test_model_aliases_are_taken(self):
        params = SearchParams.model_validate({"query": "needle", "directory": "src", "ext": "py"})
        assert params.pattern == "needle"
        assert params.path == "src"
        assert params.glob == "py"

    def test_files_mode_aliases(self):
        assert SearchParams(mode="files").mode == "files"
        assert SearchParams(mode="LIST").mode == "files"
        assert SearchParams(mode="content").mode == "content"

    def test_scope_defaults_to_the_cwd(self):
        assert SearchParams(pattern="x").scope() == Path.cwd()

    def test_context_is_clamped(self):
        assert SearchParams(pattern="x", context_lines=99).context() == 5


class TestProcess:
    def test_a_hung_backend_times_out_quickly(self, tmp_path: Path):
        running = asyncio.run(search._run(["sleep", "30"], tmp_path, timeout=0.2))
        assert running.timed_out is True
        assert running.code in (-1, -9)

    def test_a_missing_binary_is_not_a_crash(self, tmp_path: Path):
        running = asyncio.run(search._run(["definitely-not-a-binary-xyz"], tmp_path))
        assert running.code == 127
        assert running.err


class TestToolContract:
    def test_it_is_a_read_only_safe_tool(self):
        assert TOOL.name == "search"
        assert TOOL.mutating is False
        assert TOOL.parallel_safe is True

    def test_format_call(self):
        assert TOOL.format_call(SearchParams(pattern="needle")) == "needle"
        assert TOOL.format_call(SearchParams(mode="files", pattern="*.py")) == "files *.py"


class TestPatternLists:
    """`pattern` takes a list; every rung has to agree about it."""

    def test_a_literal_list_finds_every_word_on_every_rung(self, tree: Path, monkeypatch):
        # The trap: `-F` with a joined union searches for the text `a|b`, so the
        # fast rung answers "no matches" while the Python floor answers four.
        for hide_these in (("git",), ("rg",), ("rg", "git"), ("rg", "git", "fd")):
            monkeypatch.setattr(search, "_which", hide(*hide_these))
            result = find(pattern=["needle", "alpha"], literal=True, path=str(tree))
            assert result.success
            assert len(result.ui_data["rows"]) == 4, result.ui_data["backend"]

    def test_a_literal_list_via_ripgrep_is_not_the_pipe_string(self, tree: Path, monkeypatch):
        if search._which("rg") is None:
            pytest.skip("rg not installed")
        monkeypatch.setattr(search, "_which", hide("git"))
        result = find(pattern=["needle", "alpha"], literal=True, path=str(tree))
        assert result.ui_data["backend"] == "rg"
        assert len(result.ui_data["rows"]) == 4  # three needled files, plus alpha.py's own line

    def test_a_list_is_never_drawn_as_a_python_list(self, tree: Path):
        result = find(pattern=["needle", "alpha"], path=str(tree))
        assert result.ui_data["pattern"] == "needle|alpha"
        assert "[" not in result.ui_summary

    def test_format_call_joins_the_items(self):
        assert TOOL.format_call(SearchParams(pattern=["a", "b"])) == "a|b"
        assert TOOL.format_call(SearchParams(mode="files", pattern=["*.py", "*.md"])) == (
            "files *.py, *.md"
        )

    def test_the_alternatives_of_a_list_are_the_items(self):
        params = SearchParams(pattern=["a|b", "c"], literal=True)
        assert params.alternatives() == ["a|b", "c"]
        assert SearchParams(pattern=["only"]).alternatives() == []

    def test_the_old_patterns_key_still_searches(self, tree: Path):
        params = SearchParams.model_validate({"patterns": ["needle", "alpha"]})
        assert params.pattern == ["needle", "alpha"]
        assert params.query() == "needle|alpha"

    def test_a_content_glob_stays_rg_syntax(self, tree: Path):
        # `glob="tests"` means the file named `tests` to rg, not `*tests*`.
        assert search._wanted("test_a.py", SearchParams(pattern="x", glob="tests")) is False
        assert search._wanted("tests", SearchParams(pattern="x", glob="tests")) is True
        assert search._wanted("footer.py", SearchParams(mode="files", pattern="footer")) is True

    def test_counts_still_work_for_a_list(self, tree: Path):
        result = find(pattern=["needle", "absent"], path=str(tree))
        assert dict(result.ui_data["counts"]) == {"needle": 3, "absent": 0}

    def test_files_mode_unions_the_patterns_on_fd(self, tree: Path, monkeypatch):
        # `--glob` is a flag in fd and its pattern is one positional: handing it
        # two would make the second a search path, and fd answers partly.
        if search._which("fd") is None:
            pytest.skip("fd not installed")
        monkeypatch.setattr(search, "_which", hide("rg", "git"))
        result = find(mode="files", pattern=["alpha", "gamma"], path=str(tree))
        assert result.ui_data["backend"] == "fd"
        names = sorted(Path(path).name for path in result.ui_data["files"])
        assert names == ["alpha.py", "gamma.py"]


class TestEmptyStates:
    def test_a_file_scope_is_named_not_counted(self, tree: Path):
        # `no matches in 0 files` is true and useless: one file was scanned.
        result = find(pattern="absent_word", path=str(tree / "alpha.py"))
        assert result.ui_summary == "No matches in alpha.py"
        assert "0 files" not in result.result
        assert "no_ignore" not in result.result

    def test_a_tree_scope_still_counts_and_hints(self, tree: Path, monkeypatch):
        monkeypatch.setattr(search, "_which", lambda name: None)
        result = find(pattern="absent_word", path=str(tree))
        assert "3 files" in result.ui_summary
        assert "no_ignore=true" in result.result


class TestAlternativeCounts:
    """The counts line has to add up to the number printed beside it."""

    def test_counts_are_lines_not_occurrences(self, tmp_path: Path, monkeypatch):
        (tmp_path / "x.py").write_text("needle = needle_fn(1)\nneedle\n")
        for hide_these in (("git",), ("rg", "git")):
            monkeypatch.setattr(search, "_which", hide(*hide_these))
            result = find(pattern="needle|missing", path=str(tmp_path))
            counts = dict(result.ui_data["counts"])
            assert counts == {"needle": 2, "missing": 0}, result.ui_data["backend"]
            assert "2 matches" in result.ui_summary
