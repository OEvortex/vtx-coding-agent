import pytest
from textual.app import App, ComposeResult

from vtx.core.config import config
from vtx.protocol.types import FileChanges
from vtx.tui.blocks import RunSummaryBlock, ThinkingBlock, ToolBlock
from vtx.tui.chat import ChatLog
from vtx.tui.run_summary import RunSummaryStats, format_merged_summary_text
from vtx.tui.styles import get_styles


class MergedSummaryTestApp(App):
    CSS = get_styles()

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")


def test_format_merged_summary_text():
    stats = RunSummaryStats(thoughts=3, tools=4, edit_additions=12, edit_removals=5, failures=1)
    formatted = format_merged_summary_text(stats, glyph="◈")
    plain = formatted.plain

    assert "◈" in plain
    assert "Thought 3 times" in plain
    assert "Called 4 tools" in plain
    assert "Edit +12 -5" in plain
    assert "1 failure" in plain


def test_format_merged_summary_text_omissions():
    # Thought only
    stats_thought_only = RunSummaryStats(thoughts=1, tools=0)
    formatted = format_merged_summary_text(stats_thought_only)
    assert "Thought 1 time" in formatted.plain
    assert "Called" not in formatted.plain

    # Tools only
    stats_tools_only = RunSummaryStats(thoughts=0, tools=2)
    formatted = format_merged_summary_text(stats_tools_only)
    assert "Thought" not in formatted.plain
    assert "Called 2 tools" in formatted.plain


@pytest.mark.asyncio
async def test_merged_run_summaries_interleaving_and_cumulative_totals():
    async with MergedSummaryTestApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        config.ui.merged_turn_summary = True
        config.ui.collapse_after_turn = True

        chat.add_user_message("Tell me about this project")

        # Round 1: Thought + 2 Tools + commentary
        chat.start_thinking()
        chat.start_tool("read", "t1")
        chat.set_tool_result("t1", "read a.py", "", True)
        chat.start_tool("edit", "t2")
        chat.record_tool_result("t2", True, fc=FileChanges(path="a.py", added=10, removed=2))
        chat.set_tool_result("t2", "edit a.py", "", True)

        # Commentary segment 1
        chat.start_content()
        await pilot.pause()

        # Round 2: 2 Thoughts + 1 failed Tool + commentary
        chat.start_thinking()
        chat.start_thinking()
        chat.start_tool("bash", "t3")
        chat.set_tool_result("t3", "exit 1", "", False)

        # Commentary segment 2
        chat.start_content()
        await pilot.pause()

        # Complete agent run
        chat.finish_agent_run()
        await pilot.pause()

        summaries = list(chat.query(RunSummaryBlock))
        assert len(summaries) == 2

        # Verify summary 1 has cumulative totals through segment 1
        s1 = summaries[0]
        assert s1._stats.thoughts == 1
        assert s1._stats.tools == 2
        assert s1._stats.edit_additions == 10
        assert s1._stats.edit_removals == 2
        assert s1._stats.failures == 0
        assert not s1.has_class("-hidden")

        # Verify summary 2 has whole run cumulative totals
        s2 = summaries[1]
        assert s2._stats.thoughts == 3
        assert s2._stats.tools == 3
        assert s2._stats.edit_additions == 10
        assert s2._stats.edit_removals == 2
        assert s2._stats.failures == 1
        assert not s2.has_class("-hidden")

        # Verify tools and thoughts were collapsed
        thoughts = list(chat.query(ThinkingBlock))
        assert len(thoughts) == 3
        assert all(t.has_class("-hidden") for t in thoughts)

        tools = list(chat.query(ToolBlock))
        assert len(tools) == 3
        assert all(t.has_class("-run-collapsed") for t in tools)

        # Clicking summary toggles all thoughts and tools open
        chat.toggle_run_disclosure(s2)
        await pilot.pause()

        assert all(not t.has_class("-hidden") for t in thoughts)
        assert all(not t.has_class("-run-collapsed") for t in tools)

        # Clicking again closes them all
        chat.toggle_run_disclosure(s1)
        await pilot.pause()

        assert all(t.has_class("-hidden") for t in thoughts)
        assert all(t.has_class("-run-collapsed") for t in tools)


@pytest.mark.asyncio
async def test_incomplete_runs_stay_visible():
    async with MergedSummaryTestApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        config.ui.merged_turn_summary = True
        config.ui.collapse_after_turn = True

        chat.add_user_message("Incomplete run")

        # Live in-flight tool and thought
        chat.start_thinking()
        chat.start_tool("bash", "t1")
        await pilot.pause()

        # Incomplete run has not called finish_agent_run yet
        tools = list(chat.query(ToolBlock))
        assert len(tools) == 1
        assert not tools[0].has_class("-run-collapsed")

        # Native Ctrl+O toggles detail output expansion without interfering with visibility
        chat.set_tool_output_expanded(True)
        assert chat._tool_output_expanded is True
        assert tools[0]._expanded is True
