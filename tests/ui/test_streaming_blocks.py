from collections.abc import Callable

from rich.text import Text

from vtx.ui import blocks as blocks_module
from vtx.ui import formatting
from vtx.ui.blocks import ContentBlock, ThinkingBlock
from vtx.ui.formatting import find_stable_block_boundary, format_markdown

MULTI_BLOCK_DOC = (
    "Intro paragraph with a `code` span.\n"
    "\n"
    "- bullet one\n"
    "- bullet two\n"
    "\n"
    "```python\n"
    "def f():\n"
    "    return 1\n"
    "```\n"
    "\n"
    "Closing paragraph.\n"
)


def _capture_updates(block):
    updates: list[Text] = []
    # Replace the streaming update method with a list-append spy. The method
    # is declared on the class as a real function, so instance-level monkey
    # patching requires reaching into the instance dict directly.
    block.__dict__["_streaming_update_label"] = updates.append
    block.__dict__["call_after_refresh"] = lambda callback: callback()
    return updates


def _stream_lines(block, content: str) -> None:
    for line in content.splitlines(keepends=True):
        block._append_streaming(line)


def _normalize(plain: str) -> str:
    lines = [line.rstrip() for line in plain.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _count_renders(monkeypatch) -> list[str]:
    calls: list[str] = []

    def counting(text: str, width: int) -> Text:
        calls.append(text)
        return formatting.format_markdown_block(text, width)

    monkeypatch.setattr(blocks_module, "format_markdown_block", counting)
    return calls


def test_content_block_buffers_partial_line_until_newline():
    block = ContentBlock()
    updates = _capture_updates(block)

    block._append_streaming("hello")

    assert updates == []


def test_content_block_commits_completed_lines_and_buffers_tail():
    block = ContentBlock()
    updates = _capture_updates(block)

    block._append_streaming("hello\nwor")

    assert updates
    assert "hello" in updates[-1].plain
    assert not updates[-1].plain.endswith("wor")


def test_content_block_flush_finalizes_display():
    block = ContentBlock()

    block._append_streaming("hello")
    display = block._flush_streaming()

    assert display.plain.rstrip() == "hello"


def test_streaming_update_is_coalesced_until_refresh():
    block = ContentBlock()
    callbacks = []
    updates: list[Text] = []
    block.__dict__["_streaming_update_label"] = updates.append
    block.__dict__["call_after_refresh"] = callbacks.append

    block._append_streaming("a\n")
    block._append_streaming("b\n")

    assert len(callbacks) == 1
    assert updates == []

    callbacks[0]()

    assert "a" in updates[-1].plain
    assert "b" in updates[-1].plain


def test_thinking_block_buffers_partial_line_until_newline():
    block = ThinkingBlock()
    updates = _capture_updates(block)

    block._append_streaming("thinking")

    assert updates == []


def test_boundary_after_blank_line_between_paragraphs():
    assert find_stable_block_boundary("para one\n\npara two\n") == len("para one\n\n")


def test_boundary_zero_without_blank_line():
    assert find_stable_block_boundary("para one\npara two\n") == 0


def test_boundary_ignores_blank_lines_inside_fence():
    assert find_stable_block_boundary("```\ncode\n\nmore\n```\ntail") == 0


def test_boundary_tracks_tilde_fences():
    assert find_stable_block_boundary("~~~\ncode\n\nmore\n~~~\ntail") == 0


def test_committed_blocks_are_not_rerendered(monkeypatch):
    calls = _count_renders(monkeypatch)
    block = ContentBlock()
    _capture_updates(block)

    _stream_lines(block, "first para\n\nsecond para\n\nthird")

    assert sum("first para" in call for call in calls) <= 2


def test_render_empty_delta_does_not_create_blank_gap():
    block = ContentBlock()
    updates = _capture_updates(block)

    _stream_lines(block, "para one\n\n<!-- note -->\n\npara two\n")

    normalized = _normalize(updates[-1].plain)
    assert "para one" in normalized
    assert "para two" in normalized
    assert "\n\n\n" not in normalized


def test_stale_flush_callback_after_finalize_is_ignored():
    block = ContentBlock()
    callbacks: list[Callable[[], None]] = []
    updates: list[Text] = []
    block.__dict__["_streaming_update_label"] = updates.append
    block.__dict__["call_after_refresh"] = callbacks.append

    block._append_streaming("hello\n")
    block._flush_streaming()

    assert len(callbacks) == 1
    callbacks[0]()

    assert updates == []


def test_mid_stream_display_matches_full_render(monkeypatch):
    monkeypatch.setattr(blocks_module, "markdown_render_width", lambda: 80)
    block = ContentBlock()
    updates = _capture_updates(block)

    _stream_lines(block, MULTI_BLOCK_DOC)

    expected = format_markdown(MULTI_BLOCK_DOC, width=80)
    assert _normalize(updates[-1].plain) == _normalize(expected.plain)


def test_flush_is_canonical_full_render(monkeypatch):
    monkeypatch.setattr(blocks_module, "markdown_render_width", lambda: 80)
    monkeypatch.setattr(formatting, "markdown_render_width", lambda: 80)
    block = ContentBlock()
    _capture_updates(block)

    _stream_lines(block, MULTI_BLOCK_DOC)

    assert block._flush_streaming().plain == format_markdown(MULTI_BLOCK_DOC, width=80).plain


def test_width_change_invalidates_committed_cache(monkeypatch):
    width = 80
    monkeypatch.setattr(blocks_module, "markdown_render_width", lambda: width)
    calls = _count_renders(monkeypatch)
    block = ContentBlock()
    _capture_updates(block)

    _stream_lines(block, "first para\n\nsecond para\n")
    calls.clear()
    width = 60
    block._append_streaming("more text\n")

    assert any("first para" in call for call in calls)
