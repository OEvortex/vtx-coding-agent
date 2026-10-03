"""Live tool output must coalesce repaints instead of rendering per delta.

A chatty tool emits ``ToolOutputDeltaEvent`` far faster than the screen
refreshes (the turn drains the tool's output queue with no throttle). Each
live render splits the whole accumulated buffer, builds a Rich ``Text`` and
updates a widget, and each ``scroll_end()`` forces a layout pass. Doing both
per delta saturates Textual's single event loop, which also serves key
events, the spinner and Esc — so the whole app stops responding and appears
to hang.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from vtx.coding_agent.tui.chat import ChatLog
from vtx.tui.styles import get_styles


class _TestApp(App):
    CSS = get_styles()

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")


async def _settle(pilot) -> None:
    """Let queued ``call_after_refresh`` callbacks run."""
    for _ in range(4):
        await pilot.pause()


@pytest.mark.asyncio
async def test_many_live_deltas_cause_one_render():
    """200 deltas must cost one render, not 200."""
    async with _TestApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        block = chat.start_tool("bash", "t1", "$ noisy", icon="$")

        renders: list[int] = []
        original = block._render_live_output

        def spy() -> None:
            renders.append(1)
            original()

        block._render_live_output = spy  # ty:ignore[invalid-assignment]

        for i in range(200):
            chat.append_tool_output("t1", f"line {i}\n")

        # Nothing rendered synchronously: work is deferred to the next frame.
        assert renders == []
        # Every delta is still accumulated.
        assert block._live_output.count("\n") == 200

        await _settle(pilot)
        assert len(renders) == 1, f"expected 1 coalesced render, got {len(renders)}"


@pytest.mark.asyncio
async def test_live_deltas_coalesce_scroll_passes():
    """The per-delta scroll must go through the frame-coalescing helper."""
    async with _TestApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        chat.start_tool("bash", "t2", "$ noisy", icon="$")

        scrolls: list[int] = []
        original = chat._scroll_if_anchored

        def spy(*, animate: bool = False) -> None:
            scrolls.append(1)
            original(animate=animate)

        chat._scroll_if_anchored = spy  # ty:ignore[invalid-assignment]

        for i in range(200):
            chat.append_tool_output("t2", f"line {i}\n")

        assert scrolls == [], "scroll must be deferred, not run per delta"
        await _settle(pilot)
        assert len(scrolls) == 1, f"expected 1 coalesced scroll, got {len(scrolls)}"


@pytest.mark.asyncio
async def test_live_output_content_still_correct_after_coalescing():
    """Coalescing must not drop or reorder output."""
    async with _TestApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        block = chat.start_tool("bash", "t3", "$ cmd", icon="$")

        for i in range(30):
            chat.append_tool_output("t3", f"line {i}\n")
        await _settle(pilot)

        assert block._live_output.startswith("line 0\n")
        assert block._live_output.rstrip().endswith("line 29")
        shown = block.query_one("#tool-output").render().plain
        assert "line 29" in shown
