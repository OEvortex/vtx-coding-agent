"""The welcome header renders as a brand block: logo beside aligned metadata.

The metadata column and the hint cells are Rich tables, so a styled long value
has to survive rendering without widening the logo column or sliding the key
column, and each hint pair has to keep its key and description in their cells.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from vtx.tui.chat import ChatLog
from vtx.tui.styles import get_styles


class _TestApp(App):
    CSS = get_styles()

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")


class _FakeDefinition:
    name = "coding"


class _FakeAgent:
    definition = _FakeDefinition()


class _FakeRuntime:
    model = "claude-sonnet-4-5"
    model_provider = "anthropic"
    active_agent = _FakeAgent()


def _render_plain(label: Label) -> str:
    """Plain text of the label's visual, no ANSI and no Textual wrapper."""
    strips = label.visual.render_strips(100, 20, label.styles, label.app.console.options)
    return "\n".join("".join(segment.text for segment in strip) for strip in strips)


@pytest.mark.asyncio
async def test_header_shows_brand_metadata_and_aligned_hints():
    async with _TestApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        chat.add_session_info("1.2.2", runtime=_FakeRuntime())
        await pilot.pause()

        rendered = _render_plain(pilot.app.query_one(".session-info", Label))
        rows = rendered.split("\n")

        # Brand block: three logo rows, each carrying one metadata column line.
        assert len(rows) == 11, f"3 brand rows + blank + 7 hint rows expected:\n{rendered}"
        assert "VTX v1.2.2" in rows[0]
        assert "model" in rows[1] and "(anthropic) claude-sonnet-4-5" in rows[1]
        assert "agent" in rows[2] and "coding" in rows[2]

        # Metadata labels align: "coding" starts where the model id starts.
        assert rows[1].index("(") == rows[2].index("coding")

        # The logo keeps its own column on the first three rows.
        assert all(row.startswith("░") for row in rows[:3])

        # Hint keys stay in their own column: every hint row's first glyph
        # sits at the same offset, so a long key cannot slide the description.
        hint_rows = rows[4:]
        first_offset = len(next(row for row in hint_rows if row.strip())) - len(
            next(row for row in hint_rows if row.strip()).lstrip()
        )
        for row in hint_rows:
            stripped = row.strip()
            assert stripped, f"empty hint row:\n{rendered}"
            assert len(row) - len(row.lstrip()) == first_offset, f"key column slid:\n{rendered}"
        assert "/" in rendered and "shift+enter" in rendered
