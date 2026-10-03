"""Regression tests for the markdown rendering fixes.

Each test here corresponds to a defect that shipped: content that rendered as
nothing, a fence scanner that split a stream mid-block, and table rows that
silently lost values. They are easy to reintroduce, and every one of them was
invisible to the suite that existed before.
"""

from __future__ import annotations

import pytest

from vtx.agent.tools.codemode import _render_value
from vtx.tui.formatting import find_stable_block_boundary, format_markdown


def render(text: str, width: int = 44) -> list[str]:
    """Rendered lines, blanks kept, right-padding stripped."""
    return [line.rstrip() for line in format_markdown(text, width).plain.split("\n")]


def body(text: str, width: int = 44) -> list[str]:
    """Rendered lines with blank lines dropped, for indexing a table."""
    return [line for line in render(text, width) if line.strip()]


# --- HTML -------------------------------------------------------------------


@pytest.mark.parametrize(
    "src,expected",
    [
        ("<div>hello</div>", ["hello"]),
        ("<p>html para</p>", ["html para"]),
        ("<details><summary>more</summary>body</details>", ["morebody"]),
    ],
)
def test_html_block_keeps_its_text(src, expected):
    """These rendered as an empty string; the block was dropped outright."""
    assert render(src) == expected


def test_html_block_between_paragraphs_is_not_lost():
    out = render("para one\n\n<div>\nblock\n</div>\n\npara two")
    assert "block" in out
    assert out[0].startswith("para one")


def test_br_becomes_a_line_break():
    assert render("line1<br>line2") == ["line1", "line2"]


def test_inline_tag_leaves_no_phantom_blank_lines():
    out = render("x <b>y</b> z")
    assert out == ["x y z"]


def test_comment_leaves_a_single_paragraph_gap():
    assert render("before\n\n<!-- note -->\n\nafter") == ["before", "", "after"]


# --- inline code is content, not markup -------------------------------------


@pytest.mark.parametrize(
    "src,expected",
    [
        ("Use `<br>` inline", ["Use <br> inline"]),
        ("a `<div>` b", ["a <div> b"]),
        ("`<span>`", ["<span>"]),
    ],
)
def test_tags_inside_inline_code_are_preserved(src, expected):
    """A tag shown in backticks is something the writer is *describing*.

    Without this the code span rendered empty, so a reply could not mention
    `<br>` at all.
    """
    assert render(src) == expected


def test_tags_outside_inline_code_are_still_processed():
    assert render("plain `x` then <br> here") == ["plain x then", "here"]


def test_html_inside_a_fence_is_untouched():
    src = "```\n<div>sample</div>\n<br>\n```"
    assert render(src) == ["<div>sample</div>", "<br>"]


# --- fences -----------------------------------------------------------------


def test_nested_fence_does_not_end_the_stream_early():
    """A 4-backtick fence used to be closed by its inner 3-backtick one."""
    src = "para A\n\n````markdown\n```bash\necho hi\n\nmore\n```\n````\n"
    boundary = find_stable_block_boundary(src)
    assert boundary == len("para A\n\n")


def test_streamed_prefix_matches_the_final_render():
    src = "para A\n\n````markdown\n```bash\necho hi\n\nmore\n```\n````\n"
    boundary = find_stable_block_boundary(src)
    streamed = render(src[:boundary], 40)
    assert streamed == ["para A"]


def test_blank_line_inside_a_simple_fence_is_not_a_boundary():
    """The blank inside the fence must not be chosen; the one after it should be."""
    src = "a\n\n```\nx\n\ny\n```\n\nb"
    boundary = find_stable_block_boundary(src)
    assert boundary > src.index("```\nx"), "boundary fell inside the fence"
    assert boundary > src.index("\n\nb"), "boundary stopped before the blank after the fence"


# --- tables -----------------------------------------------------------------


def test_extra_delimiter_cell_still_renders_a_table():
    out = body("| a | b |\n|---|---|---|\n| 1 | 2 |")
    assert "|---" not in " ".join(out), "table degraded into raw pipe prose"
    assert out[0].startswith("a") and out[-1].startswith("1")


def test_extra_body_cell_is_not_discarded():
    out = render("| a | b |\n|---|---|\n| 1 | 2 | 3 |")
    assert "3" in out[-1]


def test_short_body_row_is_padded():
    out = body("| a | b | c |\n|---|---|---|\n| 1 |")
    assert len(out) == 3


def test_column_alignment_is_preserved():
    out = body("| left | centre | right |\n|:--|:-:|--:|\n| a | b | c |")
    assert out[0].startswith("left")


def test_table_inside_a_fence_is_not_normalised():
    src = "```\n| a | b |\n|---|---|\n| 1 |\n```"
    assert render(src) == ["| a | b |", "|---|---|", "| 1 |"]


# --- codemode return values -------------------------------------------------


def test_returned_string_keeps_its_newlines():
    """A script returning prose came back JSON-escaped, as `"a\\nb"`."""
    assert _render_value("a\nb\n\nc") == "a\nb\n\nc"


@pytest.mark.parametrize("value", [42, 1.5, True, ["a", "b"], {"k": "v"}])
def test_non_string_values_render_as_before(value):
    import json

    assert _render_value(value) == json.dumps(value, indent=2, ensure_ascii=False, default=str)


def test_none_stays_none():
    assert _render_value(None) is None
