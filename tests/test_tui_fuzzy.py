from vtx.tui.fuzzy import NO_MATCH, fuzzy_filter, fuzzy_match


def test_exact_match_outranks_subsequence():
    exact, _ = fuzzy_match("quit", "quit")
    subseq, _ = fuzzy_match("quit", "quit_cooldown_now")
    assert exact > subseq


def test_word_boundary_beats_mid_word():
    boundary, _ = fuzzy_match("mod", "settings/model")
    midword, _ = fuzzy_match("mod", "remodel")
    assert boundary > midword


def test_non_subsequence_does_not_match():
    score, positions = fuzzy_match("zzz", "abc")
    assert score == NO_MATCH
    assert positions == ()


def test_empty_query_matches_everything():
    assert fuzzy_match("", "anything") == (1.0, [])


def test_longer_query_than_text_does_not_match():
    assert fuzzy_match("abcd", "abc")[0] == NO_MATCH


def test_case_insensitive():
    assert fuzzy_match("QUIT", "quit")[0] > NO_MATCH


def test_filter_requires_every_token_to_match():
    items = ["src/app/main.py", "src/lib/util.py", "docs/readme.md"]
    result = fuzzy_filter(items, "src util", lambda s: s)
    assert result == ["src/lib/util.py"]


def test_filter_splits_on_slash():
    items = ["a/b/c.py", "a/d/e.py"]
    assert fuzzy_filter(items, "b/c", lambda s: s) == ["a/b/c.py"]


def test_filter_returns_all_for_empty_query():
    items = ["b", "a"]
    assert fuzzy_filter(items, "  ", lambda s: s) == items


def test_filter_ranks_best_first():
    items = ["model.py", "some/deep/place/with/model.py", "modal.ts"]
    result = fuzzy_filter(items, "model", lambda s: s)
    assert result[0] == "model.py"


def test_fd_query_escapes_regex_metacharacters():
    from vtx.tui.autocomplete import FilePathProvider

    # An unescaped '.' would make "app.py" also match "appXpy".
    assert FilePathProvider._fd_query("app.py") == r"app\.py"
    assert FilePathProvider._fd_query("a+b") == r"a\+b"


def test_fd_query_uses_separator_class_for_paths():
    from vtx.tui.autocomplete import FilePathProvider

    assert FilePathProvider._fd_query("vtx/tui") == r"vtx[\\/]tui"
    assert FilePathProvider._fd_query("src/") == r"src[\\/]"
