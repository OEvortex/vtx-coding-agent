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


def test_whole_token_match_outranks_a_longer_name_that_shares_its_prefix():
    """`claude-opus-5` must beat `claude-opus-5-batch`, not tie with it.

    All twelve `claude-opus-5*` ids scored identically before, because the
    exact-match bonus compared the query against the whole "label description"
    string the picker searches, which can never be equal. The winner was
    whichever the sort happened to keep.
    """
    exact = fuzzy_match("claude-opus-5", "claude-opus-5 openai")[0]
    batch = fuzzy_match("claude-opus-5", "claude-opus-5-batch openai")[0]
    fast = fuzzy_match("claude-opus-5", "claude-opus-5-fast openai")[0]
    assert exact > batch
    assert exact > fast


def test_whole_token_bonus_survives_a_namespace_prefix():
    exact = fuzzy_match("claude-opus-5", "anthropic/claude-opus-5 openrouter")[0]
    batch = fuzzy_match("claude-opus-5", "anthropic/claude-opus-5-batch openrouter")[0]
    assert exact > batch


def test_exact_full_string_still_wins():
    assert fuzzy_match("gpt-5.5", "gpt-5.5")[0] > fuzzy_match("gpt-5.5", "gpt-5.5-chat openai")[0]


def test_partial_token_still_matches_without_the_exact_bonus():
    """Subsequence matching is unchanged; only the bonus got stricter."""
    assert fuzzy_match("opus5", "claude-opus-5 openai")[0] > NO_MATCH


def test_contiguous_run_beats_a_earlier_stray_character():
    """The match must land on 'claude', not the 'c' inside 'anthropic'.

    Greedy subsequence walking found the 'c' of "anthropic" first and never
    reached the real run, so every boundary bonus was misplaced and the exact
    model scored the same as its `-batch` variant.
    """
    _, positions = fuzzy_match("claude-opus-5", "anthropic/claude-opus-5 openrouter")
    assert positions == tuple(range(10, 23))


def test_namespaced_exact_match_outranks_its_batch_variant():
    exact = fuzzy_match("claude-opus-5", "anthropic/claude-opus-5 openrouter")[0]
    batch = fuzzy_match("claude-opus-5", "anthropic/claude-opus-5-batch openrouter")[0]
    assert exact > batch


def test_subsequence_still_matches_when_nothing_is_contiguous():
    score, positions = fuzzy_match("gpt5", "gpt-5 openai")
    assert score > NO_MATCH
    assert positions == (0, 1, 2, 4)
