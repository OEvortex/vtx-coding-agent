"""The refinement block is click-to-expand, and says something either way.

The header and the summary are both click targets, and the block advertises no
key — a key name is not an affordance, and the one that used to be printed was
never wired to anything. Collapsed, the summary is the body: whitespace
collapsed and clamped, so the block still says what the pass was for. Expanded,
the summary keeps its line breaks and the quiet metadata and per-edit diffs
follow it.

A total failure also carries its `0/N` in the header. A bare "Harness
refinement failed" hides how much was rejected, and the count is the part that
tells you whether one bad edit or the whole pass went wrong.
"""

from vtx.tui.blocks import RefinementBlock, _clamp_summary, refinement_header

EDIT = {
    "action": "update",
    "kind": "memory",
    "id": "vtx-baseline",
    "applied": True,
    "after": {"title": "New title", "content": "New body"},
    "reason": "preserve across sessions",
}


def _block(summary="Record the validated fix and keep the note.", edits=None):
    return RefinementBlock(
        summary=summary,
        edits=[EDIT] if edits is None else edits,
        refinement_id="refine_1",
        scope="local",
        model="session model",
    )


def test_the_header_advertises_no_key():
    header = _block()._format_header().plain
    assert "ctrl+d" not in header
    assert header.startswith("◆ ")


def test_the_collapsed_block_shows_the_summary():
    assert "Record the validated fix" in _block()._format_summary().plain


def test_a_missing_summary_says_so_rather_than_going_blank():
    assert _block(summary="")._format_summary().plain == (
        "No summary was recorded for this harness change."
    )


def test_the_collapsed_summary_collapses_whitespace():
    block = _block(summary="line one\nline two\n\nline three")
    collapsed = block._format_summary().plain
    assert "\n" not in collapsed
    assert collapsed == "line one line two line three"


def test_the_collapsed_summary_is_clamped():
    block = _block(summary="word " * 200)
    clamped = block._format_summary().plain
    assert len(clamped) <= _block()._collapsed_summary_width()
    assert clamped.endswith("…")


def test_the_expanded_summary_keeps_its_line_breaks():
    block = _block(summary="line one\nline two")
    block.set_expanded(True)
    assert "line one\nline two" in block._format_summary().plain


def test_the_expanded_body_opens_with_metadata_not_the_summary():
    # The summary renders above the detail, so repeating it here would show it
    # twice.
    block = _block()
    block.set_expanded(True)
    detail = block._format_detail().plain
    assert "Refinement refine_1" in detail
    assert "local" in detail
    assert "Record the validated fix" not in detail


def test_the_expanded_body_carries_the_per_edit_diff():
    block = _block()
    block.set_expanded(True)
    detail = block._format_detail().plain
    assert "vtx-baseline" in detail
    assert "+ New title" in detail
    assert "preserve across sessions" in detail


def test_a_total_failure_reports_its_count_in_the_header():
    header = refinement_header(
        applied=0, total=1, kinds=["memory"], actions=["update"], rollback_of=None
    )
    assert header == "Harness refinement failed · 0/1 edits applied"


def test_a_rollback_failure_reports_its_count_too():
    header = refinement_header(
        applied=0, total=3, kinds=["memory"], actions=["update"], rollback_of="r1"
    )
    assert header == "Harness rollback failed · 0/3 edits applied"


def test_the_other_outcomes_are_unchanged():
    assert refinement_header(applied=0, total=0, kinds=[], actions=[], rollback_of=None) == (
        "Harness unchanged · no edits applied"
    )
    assert refinement_header(
        applied=1, total=3, kinds=["memory"], actions=["update"], rollback_of=None
    ) == ("Harness partially refined · 1/3 edits applied")
    assert refinement_header(
        applied=2, total=2, kinds=["memory"], actions=["create"], rollback_of=None
    ) == ("Harness refined · 2 memories created")


def test_clamp_cuts_on_a_word_boundary():
    assert _clamp_summary("alpha beta gamma delta", 20) == "alpha beta gamma…"
    assert _clamp_summary("short", 20) == "short"
