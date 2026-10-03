"""Tests for the ``CompactionBlock`` widget and the compaction event plumbing."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label, ProgressBar

from vtx.coding_agent.tui.chat import ChatLog
from vtx.core.compaction import (
    SUMMARIZATION_PROMPT,
    SUMMARY_SECTIONS,
    generate_summary,
    summary_progress,
)
from vtx.core.events import CompactionEndEvent, CompactionProgressEvent, CompactionStartEvent
from vtx.protocol.types import TextPart
from vtx.tui.blocks import CompactionBlock, _format_elapsed, _short_section_title
from vtx.tui.styles import get_styles

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _TestApp(App):
    CSS = get_styles()

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")


def _make_block(**kwargs) -> CompactionBlock:
    return CompactionBlock(**kwargs)


# ---------------------------------------------------------------------------
# Section contract
# ---------------------------------------------------------------------------


class TestSummarySections:
    def test_twelve_sections_parsed_from_prompt(self):
        assert len(SUMMARY_SECTIONS) == 12
        assert SUMMARY_SECTIONS[0] == (1, "Objective & Constraints")
        assert SUMMARY_SECTIONS[-1][0] == 12

    def test_sections_match_prompt_headings(self):
        assert all(f"## {n}. {t}" in SUMMARIZATION_PROMPT for n, t in SUMMARY_SECTIONS)

    def test_progress_ignores_analysis_scratchpad(self):
        text = (
            "<analysis>\n## 1. Objective & Constraints\nscratch\n</analysis>\n"
            "<summary>\nx\n</summary>"
        )
        assert summary_progress(text) == []

    def test_progress_reads_summary_block(self):
        text = (
            "<summary>\n## 1. Objective & Constraints\na\n## 2. All User Messages\nb\n</summary>"
        )
        assert summary_progress(text) == [(1, "Objective & Constraints"), (2, "All User Messages")]

    def test_short_titles_are_compact(self):
        assert _short_section_title("All User Messages") == "User Messages"
        assert _short_section_title("Interface Contracts & Key Code").startswith("Interface")
        assert all(len(_short_section_title(t)) <= 14 for _, t in SUMMARY_SECTIONS)


class TestElapsed:
    @pytest.mark.parametrize(
        ("seconds", "expected"), [(0.4, "0.4s"), (9.0, "9.0s"), (42.0, "42s"), (125.0, "2m05s")]
    )
    def test_formats(self, seconds, expected):
        assert _format_elapsed(seconds) == expected


# ---------------------------------------------------------------------------
# Header states
# ---------------------------------------------------------------------------


class TestCompactionHeader:
    def test_running_header_shows_usage_and_trigger(self):
        block = _make_block(tokens_before=138_200, context_window=200_000, trigger="overflow")
        header = block._format_header().plain
        assert "Compacting" in header
        assert "138k/200k (69%)" in header
        assert "auto-compaction" in header

    def test_running_header_colour_escalates_past_threshold(self):
        from vtx.core.config import config

        colors = config.ui.colors
        low = _make_block(tokens_before=100_000, context_window=200_000)
        high = _make_block(tokens_before=190_000, context_window=200_000)
        usage_styles = {low._format_header().spans[3].style, high._format_header().spans[3].style}
        assert usage_styles == {colors.muted, colors.notice}
        assert colors.muted != colors.notice

    def test_finished_header_shows_before_after_and_saving(self):
        block = _make_block(tokens_before=100_000)
        block.finish(tokens_after=20_000, summary="x")
        header = block._format_header().plain
        assert "Compacted" in header
        assert "100k → 20k" in header
        assert f"(−{80}%)" in header  # noqa: RUF001
        assert "view summary" in header

    def test_error_header_shows_reason(self):
        block = _make_block(tokens_before=10)
        block.finish(tokens_after=0, error="provider 500")
        header = block._format_header().plain
        assert "Compaction failed" in header
        assert "provider 500" in header

    def test_manual_trigger_label(self):
        block = _make_block(trigger="manual")
        assert "requested" in block._format_header().plain


class TestCompactionSections:
    def test_checklist_ticks_started_sections(self):
        block = _make_block()
        block.update_progress(500, [(1, "Objective & Constraints"), (2, "All User Messages")])
        rendered = block._format_sections().plain
        assert rendered.count("✓") == 2
        assert "Objective" in rendered

    def test_checklist_shows_drafting_before_any_section(self):
        block = _make_block()
        assert "drafting handoff" in block._format_sections().plain

    def test_progress_after_finish_is_ignored(self):
        block = _make_block()
        block.finish(tokens_after=10, summary="s")
        block.update_progress(900, [(1, "Objective & Constraints")])
        assert "✓" not in block._format_sections().plain

    def test_empty_update_does_not_clear_the_checklist(self):
        block = _make_block()
        block.update_progress(500, [(1, "Objective & Constraints")])
        block.update_progress(600, [])
        assert "✓ Objective" in block._format_sections().plain


class TestCompactionToggle:
    def test_toggle_noop_before_finish(self):
        block = _make_block()
        assert block.toggle_expanded() is False

    def test_toggle_noop_without_summary(self):
        block = _make_block()
        block.finish(tokens_after=10)
        assert block.toggle_expanded() is False

    def test_toggle_flips_expanded_state(self):
        block = _make_block()
        block.finish(tokens_after=10, summary="## 1. Objective\nbody")
        assert block.toggle_expanded() is True
        assert block._expanded is True
        assert block.toggle_expanded() is False


# ---------------------------------------------------------------------------
# Mounted widget behaviour
# ---------------------------------------------------------------------------


class TestCompactionBlockMounted:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_bar_is_indeterminate_while_running(self):
        app = _TestApp()
        async with app.run_test(size=(100, 24)) as pilot:
            chat = app.query_one("#chat-log", ChatLog)
            chat.start_compaction(tokens_before=1000, context_window=2000, trigger="overflow")
            await pilot.pause()
            bar = chat.query_one("#compaction-bar", ProgressBar)
            assert bar.total is None

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_bar_reflects_tokens_after_when_done(self):
        app = _TestApp()
        async with app.run_test(size=(100, 24)) as pilot:
            chat = app.query_one("#chat-log", ChatLog)
            chat.start_compaction(tokens_before=1000)
            await pilot.pause()
            chat.finish_compaction(tokens_before=1000, tokens_after=250, summary="s")
            await pilot.pause()
            bar = chat.query_one("#compaction-bar", ProgressBar)
            assert bar.total == 1000
            assert bar.progress == 250

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_summary_hidden_until_expanded(self):
        app = _TestApp()
        async with app.run_test(size=(100, 24)) as pilot:
            chat = app.query_one("#chat-log", ChatLog)
            block = chat.start_compaction(tokens_before=1000)
            await pilot.pause()
            chat.finish_compaction(tokens_before=1000, tokens_after=100, summary="# Handoff\ntext")
            await pilot.pause()
            label = chat.query_one("#compaction-summary", Label)
            assert label.has_class("-hidden")
            block.toggle_expanded()
            await pilot.pause()
            assert not label.has_class("-hidden")
            assert "Handoff" in label.content.plain

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_progress_reaches_the_block(self):
        app = _TestApp()
        async with app.run_test(size=(100, 24)) as pilot:
            chat = app.query_one("#chat-log", ChatLog)
            block = chat.start_compaction(tokens_before=1000)
            await pilot.pause()
            chat.update_compaction_progress(400, [(1, "Objective & Constraints")])
            await pilot.pause()
            assert block._chars == 400
            assert block._sections == [(1, "Objective & Constraints")]

    @pytest.mark.asyncio
    async def test_checklist_visible_while_running_and_hidden_when_done(self):
        app = _TestApp()
        async with app.run_test(size=(100, 24)) as pilot:
            chat = app.query_one("#chat-log", ChatLog)
            block = chat.start_compaction(tokens_before=1000)
            await pilot.pause()
            label = chat.query_one("#compaction-sections", Label)
            assert not label.has_class("-hidden")
            assert "drafting handoff" in label.content.plain

            block.update_progress(200, [(1, "Objective & Constraints")])
            await pilot.pause()
            assert not label.has_class("-hidden")
            assert "✓ Objective" in label.content.plain

            chat.finish_compaction(tokens_before=1000, tokens_after=100, summary="s")
            await pilot.pause()
            assert label.has_class("-hidden")

    @pytest.mark.asyncio
    async def test_checklist_hidden_on_error(self):
        app = _TestApp()
        async with app.run_test(size=(100, 24)) as pilot:
            chat = app.query_one("#chat-log", ChatLog)
            chat.start_compaction(tokens_before=1000)
            await pilot.pause()
            chat.finish_compaction(tokens_before=1000, tokens_after=0, error="boom")
            await pilot.pause()
            assert chat.query_one("#compaction-sections", Label).has_class("-hidden")

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_add_compaction_message_mounts_finished_block(self):
        app = _TestApp()
        async with app.run_test(size=(100, 24)) as pilot:
            chat = app.query_one("#chat-log", ChatLog)
            chat.add_compaction_message(200_000, 40_000, "## 1. Objective\ns")
            await pilot.pause()
            block = chat.query_one(CompactionBlock)
            assert block._finished is True
            assert block._summary == "## 1. Objective\ns"

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_unmounted_progress_update_is_ignored(self):
        app = _TestApp()
        async with app.run_test(size=(100, 24)) as pilot:
            chat = app.query_one("#chat-log", ChatLog)
            chat.update_compaction_progress(10, [(1, "Objective & Constraints")])
            await pilot.pause()
            assert not chat.query(CompactionBlock)


# ---------------------------------------------------------------------------
# Event shapes
# ---------------------------------------------------------------------------


class TestCompactionEvents:
    def test_start_event_carries_context(self):
        event = CompactionStartEvent(tokens_before=1000, context_window=2000, trigger="kernel")
        assert (event.tokens_before, event.context_window, event.trigger) == (1000, 2000, "kernel")

    def test_end_event_carries_summary(self):
        event = CompactionEndEvent(tokens_before=10, tokens_after=2, summary="s")
        assert event.summary == "s"

    def test_progress_event_defaults(self):
        event = CompactionProgressEvent()
        assert event.chars == 0
        assert event.sections_started == []


# ---------------------------------------------------------------------------
# Streaming callback
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield TextPart(text=c)

        return gen()


class _FakeProvider:
    def __init__(self, chunks):
        self._chunks = chunks

    async def stream(self, messages, system_prompt=None, tools=None, thinking_level=None):
        return _FakeStream(self._chunks)


class TestGenerateSummaryProgress:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_on_delta_reports_cumulative_sections(self):
        provider = _FakeProvider(["<summary>\n## 1. Objective", " & Constraints\na\n## 2. All"])
        seen: list[tuple[int, int]] = []
        await generate_summary(
            [], provider, on_delta=lambda p: seen.append((p.chars, len(p.sections_started)))
        )
        assert seen[-1][0] == sum(
            len(c) for c in ["<summary>\n## 1. Objective", " & Constraints\na\n## 2. All"]
        )
        assert seen[-1][1] == 2
        # Section count never regresses.
        counts = [c for _, c in seen]
        assert counts == sorted(counts)

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_focus_instructions_precede_summarization_prompt(self):
        provider = _FakeProvider(["ok"])
        captured: list[list] = []

        async def stream(messages, system_prompt=None, tools=None, thinking_level=None):
            captured.append(list(messages))
            return _FakeStream(["ok"])

        provider.stream = stream  # type: ignore[method-assign]
        await generate_summary([], provider, focus_instructions="focus on the parser bug")
        texts = [getattr(m, "content", "") for m in captured[0]]
        assert any("focus on the parser bug" in str(t) for t in texts)
        assert SUMMARIZATION_PROMPT in str(texts[-1])
