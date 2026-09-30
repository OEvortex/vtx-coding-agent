"""Tests for the ``/harness list`` block in the chat log.

A memory summary is a long single line, and the block used to hand it to Rich
as one logical line: Rich then wrapped at the label's measured width rather than
the visible one, so long summaries ran off the right edge and wrapped back to
column 0. These pin the wrapped output instead of the styling.
"""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from vtx.tui.chat import ChatLog
from vtx.tui.styles import get_styles

WIDTH = 100


class HarnessApp(App):
    CSS = get_styles()

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")


def _entry(**overrides):
    entry = type(
        "Entry",
        (),
        {
            "id": "vtx-refactor-active-bugs",
            "kind": "memory",
            "path": "vtx/mcp",
            "scope": "local",
            "version": 1,
            "title": "X continuation: MCP README and real-server validation",
            "content": (
                "Current VTX MCP progress: - `src/vtx/mcp/README.md` was rewritten in "
                "the structure and tone of `ref /pi-mono/packages/mcp/README.md`: concise "
                "package introduction, runnable client example, sections on config, the "
                "tool adapter, OAuth, supported protocol surface, and testing. - Real-server "
                "validation passed for the ddg-search stdio server."
            ),
        },
    )()
    for key, value in overrides.items():
        setattr(entry, key, value)
    return entry


def _rendered_lines(chat: ChatLog) -> list[str]:
    label = next(
        child
        for child in chat.children
        if isinstance(child, Label) and "harness-entries" in child.classes
    )
    return label.content.plain.splitlines()


@pytest.mark.asyncio
async def test_a_long_summary_wraps_inside_the_visible_width():
    async with HarnessApp().run_test(size=(WIDTH, 30)) as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        chat.add_harness_entries([_entry()], title="Harness entries")
        await pilot.pause()

        body = [
            line for line in _rendered_lines(chat) if "README.md" in line or "MCP progress" in line
        ]
        assert body, _rendered_lines(chat)
        for line in body:
            assert len(line) <= WIDTH - 2, line


@pytest.mark.asyncio
async def test_a_wrapped_continuation_keeps_the_indent():
    """The old shape wrapped back to column 0, so a summary read as a new field."""
    async with HarnessApp().run_test(size=(WIDTH, 30)) as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        chat.add_harness_entries([_entry()], title="Harness entries")
        await pilot.pause()

        lines = _rendered_lines(chat)
        first = next(i for i, line in enumerate(lines) if "MCP progress" in line)
        continuation = lines[first + 1]

        assert continuation.startswith("    ")
        assert continuation.strip(), "summary should continue onto another line"


@pytest.mark.asyncio
async def test_a_long_summary_is_truncated_to_a_bounded_number_of_lines():
    async with HarnessApp().run_test(size=(WIDTH, 30)) as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        chat.add_harness_entries([_entry(content="word " * 400)], title="Harness entries")
        await pilot.pause()

        summary_lines = [line for line in _rendered_lines(chat) if line.startswith("    ")]

        assert 0 < len(summary_lines) <= 5
        assert summary_lines[-1].endswith("...")


@pytest.mark.asyncio
async def test_a_single_entry_is_counted_in_the_singular():
    async with HarnessApp().run_test(size=(WIDTH, 30)) as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        chat.add_harness_entries([_entry()], title="Harness entries")
        await pilot.pause()

        assert any("1 entry ·" in line for line in _rendered_lines(chat))


@pytest.mark.asyncio
async def test_a_short_summary_is_left_alone():
    async with HarnessApp().run_test(size=(WIDTH, 30)) as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        chat.add_harness_entries([_entry(content="Short.")], title="Harness entries")
        await pilot.pause()

        assert "    Short." in _rendered_lines(chat)
