"""``@path`` token parsing, candidate listing and prompt expansion."""

import os

from vx.atpath import expand_paths, is_boundary, list_candidates, parse_at


class TestIsBoundary:
    def test_space_and_brackets_are_boundaries(self):
        assert is_boundary(" ")
        assert is_boundary("(")
        assert is_boundary("<")
        assert is_boundary("'")

    def test_letter_is_not(self):
        assert not is_boundary("x")
        assert not is_boundary("@")


class TestParseAt:
    def test_simple_token(self):
        text = "look at @src/main.py please"
        cursor = text.index("main") + 4
        result = parse_at(text, cursor)
        assert result is not None
        at, _end, query = result
        assert text[at] == "@"
        assert query == "src/main"

    def test_no_at_returns_none(self):
        assert parse_at("hello world", 5) is None

    def test_at_inside_word_is_not_a_token(self):
        # The @ must follow a boundary; "a@b" is not a path token.
        assert parse_at("email a@b.com", 8) is None

    def test_quoted_token(self):
        text = 'open @"my dir/file.txt" now'
        cursor = text.index("file") + 2
        result = parse_at(text, cursor)
        assert result is not None
        _at, end, query = result
        assert query == "my dir/fi"
        # replace_end covers the closing quote too
        assert text[end - 1] == '"'

    def test_cursor_before_path_returns_none(self):
        text = "@foo"
        assert parse_at(text, 0) is None  # cursor before the @

    def test_cursor_past_token_returns_none(self):
        text = "@foo bar"
        assert parse_at(text, 8) is None


class TestListCandidates:
    def test_lists_files_and_dirs(self, tmp_path):
        (tmp_path / "alpha.py").write_text("x")
        (tmp_path / "beta").mkdir()
        (tmp_path / "beta" / "inner.py").write_text("x")
        candidates = list_candidates(str(tmp_path), "")
        names = {os.path.basename(c) for c in candidates}
        assert "alpha.py" in names
        assert "beta" in names
        assert "inner.py" in names

    def test_query_filters(self, tmp_path):
        (tmp_path / "keep_me.py").write_text("x")
        (tmp_path / "drop.txt").write_text("x")
        candidates = list_candidates(str(tmp_path), "keep")
        assert candidates == ["keep_me.py"]

    def test_skips_hidden_and_cache_dirs(self, tmp_path):
        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".hidden" / "secret.py").write_text("x")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "mod.pyc").write_text("x")
        candidates = list_candidates(str(tmp_path), "")
        assert all(".hidden" not in c and "__pycache__" not in c for c in candidates)

    def test_respects_limit(self, tmp_path):
        for i in range(50):
            (tmp_path / f"f{i:02d}.py").write_text("x")
        assert len(list_candidates(str(tmp_path), "", limit=10)) == 10


class TestExpandPaths:
    def test_relative_path_becomes_absolute(self, tmp_path):
        out = expand_paths("read @src/main.py", str(tmp_path))
        assert out == f"read @{tmp_path}/src/main.py"

    def test_leading_dot_slash_stripped(self, tmp_path):
        out = expand_paths("@./a.py", str(tmp_path))
        assert out == f"@{tmp_path}/a.py"

    def test_absolute_path_untouched(self, tmp_path):
        out = expand_paths(f"@{tmp_path}/a.py", "/somewhere/else")
        assert out == f"@{tmp_path}/a.py"

    def test_quoted_path(self, tmp_path):
        out = expand_paths('@"my file.py"', str(tmp_path))
        assert out == f'@"{tmp_path}/my file.py"'

    def test_non_token_at_untouched(self):
        assert expand_paths("email me at a@b.com", "/tmp") == "email me at a@b.com"

    def test_prose_without_at_unchanged(self):
        text = "no tokens here"
        assert expand_paths(text, "/tmp") == text
