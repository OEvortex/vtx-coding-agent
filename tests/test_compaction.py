import asyncio
from types import SimpleNamespace
from typing import cast

import pytest

from vtx.agent.loop import Agent, AgentConfig
from vtx.agent.runtime import ConversationRuntime
from vtx.agent.session import CompactionEntry, Session
from vtx.ai.providers.mock import MockProvider
from vtx.core.compaction import is_overflow
from vtx.core.config import Config
from vtx.core.events import CompactionEndEvent
from vtx.protocol.types import (
    AssistantMessage,
    StopReason,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from vtx.tui.agent_runner import AgentRunnerMixin
from vtx.tui.commands import CommandsMixin
from vtx.tui.widgets import InfoBar

# ---------------------------------------------------------------------------
# is_overflow tests
# ---------------------------------------------------------------------------


class TestIsOverflow:
    def test_below_threshold(self):
        # 50% of 200k -> no overflow at 80%
        usage = Usage(input_tokens=100_000, output_tokens=5_000)
        assert not is_overflow(usage, context_window=200_000, threshold_percent=80.0)

    def test_at_threshold(self):
        # 80% of 200k = 160k -> overflow at exactly 80%
        usage = Usage(input_tokens=160_000)
        assert is_overflow(usage, context_window=200_000, threshold_percent=80.0)

    def test_above_threshold(self):
        # 97.5% of 200k -> overflow
        usage = Usage(input_tokens=190_000, output_tokens=5_000)
        assert is_overflow(usage, context_window=200_000, threshold_percent=80.0)

    def test_cache_tokens_counted(self):
        # total = 100k + 5k + 50k + 30k = 185k = 92.5% of 200k -> overflow
        usage = Usage(
            input_tokens=100_000,
            output_tokens=5_000,
            cache_read_tokens=50_000,
            cache_write_tokens=30_000,
        )
        assert is_overflow(usage, context_window=200_000, threshold_percent=80.0)

    def test_custom_threshold_lower(self):
        # 50% of 200k = 100k -> overflow at 50% threshold
        usage = Usage(input_tokens=100_000)
        assert is_overflow(usage, context_window=200_000, threshold_percent=50.0)

    def test_custom_threshold_higher(self):
        # 90% of 200k = 180k -> 170k is not overflow
        usage = Usage(input_tokens=170_000)
        assert not is_overflow(usage, context_window=200_000, threshold_percent=90.0)

    def test_zero_usage_no_overflow(self):
        usage = Usage()
        assert not is_overflow(usage, context_window=200_000, threshold_percent=80.0)

    def test_exact_boundary(self):
        # 80% of 200k = 160k -> exactly at boundary -> overflow
        usage = Usage(input_tokens=160_000)
        assert is_overflow(usage, context_window=200_000, threshold_percent=80.0)

    def test_one_below_boundary(self):
        usage = Usage(input_tokens=159_999)
        assert not is_overflow(usage, context_window=200_000, threshold_percent=80.0)


# ---------------------------------------------------------------------------
# session.messages compacted view tests
# ---------------------------------------------------------------------------


class TestSessionCompactedMessages:
    def test_no_compaction_returns_all_messages(self):
        session = Session.in_memory()
        session.append_message(UserMessage(content="Hello"))
        session.append_message(AssistantMessage(content=[TextContent(text="Hi")]))
        session.append_message(UserMessage(content="How are you?"))

        assert len(session.messages) == 3
        assert session.messages[0].role == "user"
        assert session.messages[1].role == "assistant"
        assert session.messages[2].role == "user"

    def test_compaction_filters_old_messages(self):
        session = Session.in_memory()

        # Old conversation
        session.append_message(UserMessage(content="Old question 1"))
        session.append_message(AssistantMessage(content=[TextContent(text="Old answer 1")]))
        session.append_message(UserMessage(content="Old question 2"))
        session.append_message(AssistantMessage(content=[TextContent(text="Old answer 2")]))

        # Compaction
        session.append_compaction(
            summary="User asked two questions and got answers.",
            first_kept_entry_id=session.leaf_id or "",
            tokens_before=50_000,
        )

        # New conversation after compaction
        session.append_message(UserMessage(content="New question"))
        session.append_message(AssistantMessage(content=[TextContent(text="New answer")]))

        messages = session.messages

        # Should be: synthetic user + synthetic assistant (summary) + new user + new assistant
        assert len(messages) == 4
        assert messages[0].role == "user"
        assert messages[0].role == "user"
        assert "Context compacted" in messages[0].content
        assert messages[1].role == "assistant"
        assistant = messages[1]
        assert isinstance(assistant, AssistantMessage)
        assert isinstance(assistant.content[0], TextContent)
        assert assistant.content[0].text == "User asked two questions and got answers."
        assert messages[2].role == "user"
        assert messages[2].content == "New question"
        assert messages[3].role == "assistant"

    def test_all_messages_returns_everything(self):
        session = Session.in_memory()

        session.append_message(UserMessage(content="Old question"))
        session.append_message(AssistantMessage(content=[TextContent(text="Old answer")]))

        session.append_compaction(
            summary="Summary", first_kept_entry_id=session.leaf_id or "", tokens_before=50_000
        )

        session.append_message(UserMessage(content="New question"))

        # all_messages ignores compaction, returns all MessageEntry messages
        assert len(session.all_messages) == 3
        assert session.all_messages[0].content == "Old question"
        assert session.all_messages[2].content == "New question"

    def test_compaction_with_no_messages_after(self):
        session = Session.in_memory()

        session.append_message(UserMessage(content="Question"))
        session.append_message(AssistantMessage(content=[TextContent(text="Answer")]))

        session.append_compaction(
            summary="Had a Q&A.", first_kept_entry_id=session.leaf_id or "", tokens_before=30_000
        )

        messages = session.messages

        # Only synthetic user + assistant summary, no messages after compaction
        assert len(messages) == 2
        assert "Context compacted" in messages[0].content
        assistant = messages[1]
        assert isinstance(assistant, AssistantMessage)
        content = assistant.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "Had a Q&A."

    def test_multiple_compactions_uses_last(self):
        session = Session.in_memory()

        session.append_message(UserMessage(content="Q1"))
        session.append_message(AssistantMessage(content=[TextContent(text="A1")]))

        session.append_compaction(
            summary="First summary",
            first_kept_entry_id=session.leaf_id or "",
            tokens_before=30_000,
        )

        session.append_message(UserMessage(content="Q2"))
        session.append_message(AssistantMessage(content=[TextContent(text="A2")]))

        session.append_compaction(
            summary="Second summary (includes first)",
            first_kept_entry_id=session.leaf_id or "",
            tokens_before=60_000,
        )

        session.append_message(UserMessage(content="Q3"))

        messages = session.messages

        # Should use second compaction's summary
        assert len(messages) == 3
        assert "Context compacted" in messages[0].content
        assistant = messages[1]
        assert isinstance(assistant, AssistantMessage)
        content = assistant.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "Second summary (includes first)"
        assert messages[2].content == "Q3"

    def test_compaction_preserves_tool_results_after(self):
        session = Session.in_memory()

        session.append_message(UserMessage(content="Old"))
        session.append_message(AssistantMessage(content=[TextContent(text="Old answer")]))

        session.append_compaction(
            summary="Summary", first_kept_entry_id=session.leaf_id or "", tokens_before=40_000
        )

        # New turn with tool calls
        session.append_message(UserMessage(content="Read file.txt"))
        session.append_message(
            AssistantMessage(
                content=[ToolCall(id="t1", name="read", arguments={"path": "file.txt"})]
            )
        )
        session.append_message(
            ToolResultMessage(
                tool_call_id="t1", tool_name="read", content=[TextContent(text="file contents")]
            )
        )

        messages = session.messages

        # synthetic pair + user + assistant (tool call) + tool result
        assert len(messages) == 5
        assert messages[3].role == "assistant"
        assert messages[4].role == "tool_result"


# ---------------------------------------------------------------------------
# Compaction entry persistence tests
# ---------------------------------------------------------------------------


class TestCompactionPersistence:
    def test_compaction_entry_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("vtx.agent.session.Session.get_sessions_dir", lambda cwd: tmp_path)

        session = Session.create("/test/project")
        session.append_message(UserMessage(content="Hello"))
        session.append_message(AssistantMessage(content=[TextContent(text="Hi")]))

        session.append_compaction(
            summary="Test summary",
            first_kept_entry_id=session.leaf_id or "",
            tokens_before=42_000,
            details={"model": "test"},
        )

        # Need another assistant message after compaction for persistence
        session.append_message(UserMessage(content="Continue"))
        session.append_message(AssistantMessage(content=[TextContent(text="OK")]))

        session_file = session.session_file
        assert session_file is not None
        loaded = Session.load(session_file)

        compaction_entries = [e for e in loaded.entries if isinstance(e, CompactionEntry)]
        assert len(compaction_entries) == 1
        assert compaction_entries[0].summary == "Test summary"
        assert compaction_entries[0].tokens_before == 42_000
        assert compaction_entries[0].details == {"model": "test"}

    def test_loaded_session_messages_are_compacted(self, tmp_path, monkeypatch):
        monkeypatch.setattr("vtx.agent.session.Session.get_sessions_dir", lambda cwd: tmp_path)

        session = Session.create("/test/project")
        session.append_message(UserMessage(content="Old"))
        session.append_message(AssistantMessage(content=[TextContent(text="Old reply")]))

        session.append_compaction(
            summary="We discussed old stuff.",
            first_kept_entry_id=session.leaf_id or "",
            tokens_before=50_000,
        )

        session.append_message(UserMessage(content="New"))
        session.append_message(AssistantMessage(content=[TextContent(text="New reply")]))

        session_file = session.session_file
        assert session_file is not None
        loaded = Session.load(session_file)

        # messages should be compacted view
        messages = loaded.messages
        assert len(messages) == 4
        assert "Context compacted" in messages[0].content
        assistant = messages[1]
        assert isinstance(assistant, AssistantMessage)
        content = assistant.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "We discussed old stuff."
        assert messages[2].content == "New"

        # all_messages should have everything
        assert len(loaded.all_messages) == 4
        assert loaded.all_messages[0].content == "Old"


# ---------------------------------------------------------------------------
# Regression tests for usage-less latest assistant messages
# ---------------------------------------------------------------------------


class _TestCommandsApp(CommandsMixin):
    def __init__(
        self, session: Session, provider: MockProvider, chat, system_prompt: str = "test"
    ) -> None:
        self._session = session
        self._provider = provider
        self._agent = SimpleNamespace(system_prompt=system_prompt)
        self._is_running = False
        self._chat = chat
        # A real, unmounted InfoBar: it renders into suppressed lookups, so
        # the counters are readable without standing up a Textual app.
        self.info_bar = InfoBar(".", "mock-model", context_window=200_000)
        self._runtime = ConversationRuntime(
            cwd=str(session.cwd),
            model="mock-model",
            model_provider="mock",
            api_key=None,
            base_url=None,
            thinking_level="high",
            tools=[],
        )
        self._runtime.provider = self._provider
        self._runtime.session = self._session
        self._runtime.agent = cast(Agent, self._agent)

    def query_one(self, selector: str, cls):
        if selector == "#chat-log":
            return self._chat
        if selector == "#compact-footer":
            return self.info_bar
        raise LookupError(selector)

    def _sync_runtime_state(self) -> None:
        self._provider = self._runtime.provider
        self._session = self._runtime.session
        self._agent = self._runtime.agent


class TestCompactionUsageBacktracking:
    def test_manual_compaction_without_messages_is_error(self, fake_chat):
        session = Session.in_memory()
        provider = MockProvider()
        app = _TestCommandsApp(session=session, provider=provider, chat=fake_chat)

        app._handle_compact_command()

        assert fake_chat.errors == ["No conversation to compact"]
        assert fake_chat.infos == []

    @pytest.mark.asyncio
    async def test_manual_compaction_uses_latest_assistant_with_usage(
        self, monkeypatch, fake_chat
    ):
        session = Session.in_memory()
        session.append_message(UserMessage(content="hi"))
        session.append_message(
            AssistantMessage(
                content=[TextContent(text="usable")],
                usage=Usage(
                    input_tokens=100, output_tokens=50, cache_read_tokens=10, cache_write_tokens=5
                ),
            )
        )
        session.append_message(
            AssistantMessage(
                content=[TextContent(text="interrupted")],
                usage=None,
                stop_reason=StopReason.INTERRUPTED,
            )
        )

        provider = MockProvider()
        app = _TestCommandsApp(session=session, provider=provider, chat=fake_chat)

        async def _fake_summary(*args, **kwargs):
            return "summary"

        monkeypatch.setattr("vtx.agent.runtime.generate_summary", _fake_summary)

        await app._do_compact()

        assert fake_chat.errors == []
        assert fake_chat.compaction_tokens == 165
        compaction_entries = [e for e in session.entries if isinstance(e, CompactionEntry)]
        assert len(compaction_entries) == 1
        assert compaction_entries[0].tokens_before == 165

    @pytest.mark.asyncio
    async def test_auto_compaction_uses_latest_assistant_with_usage(self, monkeypatch):
        session = Session.in_memory()
        session.append_message(UserMessage(content="hi"))
        session.append_message(
            AssistantMessage(
                content=[TextContent(text="usable")],
                usage=Usage(
                    input_tokens=3000,
                    output_tokens=500,
                    cache_read_tokens=100,
                    cache_write_tokens=50,
                ),
            )
        )
        session.append_message(
            AssistantMessage(
                content=[TextContent(text="interrupted")],
                usage=None,
                stop_reason=StopReason.INTERRUPTED,
            )
        )

        provider = MockProvider()
        agent = Agent(
            provider=provider,
            tools=[],
            session=session,
            system_prompt="system",
            config=AgentConfig(context_window=1000, max_output_tokens=1),
        )

        async def _fake_summary(*args, **kwargs):
            return "summary"

        monkeypatch.setattr("vtx.agent.loop.generate_summary", _fake_summary)

        events = [e async for e in agent._check_compaction(StopReason.STOP, "system", None)]
        assert [e.type for e in events] == ["compaction_start", "compaction_end"]

        end_event = events[1]
        assert end_event.type == "compaction_end"
        assert end_event.tokens_before == 3650

        compaction_entries = [e for e in session.entries if isinstance(e, CompactionEntry)]
        assert len(compaction_entries) == 1
        assert compaction_entries[0].tokens_before == 3650


# ---------------------------------------------------------------------------
# The info bar must track compaction without waiting for the next message
# ---------------------------------------------------------------------------


class _FakeChatAndStatus:
    """Stand-ins for the chat log and status line in the event renderer."""

    def __init__(self) -> None:
        self.compaction_calls: list[tuple[int, int]] = []
        self.started: list[tuple[int, int, str]] = []
        self.errors: list[str] = []

    def start_compaction(
        self, *, tokens_before: int = 0, context_window: int = 0, trigger: str = ""
    ) -> None:
        self.started.append((tokens_before, context_window, trigger))

    def update_compaction_progress(self, _chars: int, _sections: list) -> None:
        return None

    def finish_compaction(
        self, *, tokens_before: int = 0, tokens_after: int = 0, **_kwargs
    ) -> None:
        self.compaction_calls.append((tokens_before, tokens_after))

    def end_block(self) -> None:
        return None

    def add_info_message(self, message: str, error: bool = False, **_kwargs) -> None:
        if error:
            self.errors.append(message)

    def set_agent_state(self, _state: str | None) -> None:
        return None

    def set_active_tool(self, _name: str | None) -> None:
        return None


class _TestRunnerApp(AgentRunnerMixin):
    """Hosts ``_render_agent_event`` without the real Textual app.

    The method only touches the chat log, status line and info bar, so three
    query_one targets are all it needs.
    """

    def __init__(self, session: Session) -> None:
        self._runtime = SimpleNamespace(session=session)
        self.chat = _FakeChatAndStatus()
        self.status = _FakeChatAndStatus()
        self.info_bar = InfoBar(".", "mock-model", context_window=200_000)
        # Per-run state the renderer reads; a real run sets these in
        # _run_agent_inner before the first event arrives.
        self._current_block_type = None
        self._turn_started = None

    def query_one(self, selector: str, cls):
        if selector == "#chat-log":
            return self.chat
        if selector == "#status-line":
            return self.status
        if selector == "#compact-footer":
            return self.info_bar
        raise LookupError(selector)


def _session_awaiting_compaction() -> Session:
    """A session whose last turn pushed it to 180k of a 200k window."""
    session = Session.in_memory()
    session.append_message(UserMessage(content="hi"))
    session.append_message(
        AssistantMessage(
            content=[TextContent(text="a long answer")],
            usage=Usage(input_tokens=180_000, output_tokens=500),
        )
    )
    return session


class TestInfoBarFollowsCompaction:
    """The context figure must shrink the moment compaction lands.

    ``InfoBar.update_tokens`` only ever sees a turn's provider usage, so after
    a compaction the bar kept quoting the pre-shrink number until the user sent
    another message. Each test asserts against the rendered row rather than the
    private field, because the rendered row is what the user actually reads.
    """

    @pytest.mark.asyncio
    async def test_auto_compaction_updates_the_bar_without_a_new_message(self, monkeypatch):
        session = _session_awaiting_compaction()
        app = _TestRunnerApp(session)
        # The bar is mid-turn: 180k is what the last TurnEndEvent reported.
        app.info_bar.update_tokens(180_000, 500)
        assert "180k/200k" in app.info_bar._format_row1_right().plain

        async def _fake_summary(*args, **kwargs):
            return "short summary"

        monkeypatch.setattr("vtx.agent.loop.generate_summary", _fake_summary)

        for event in await _auto_compaction_events(session):
            await app._render_agent_event(event, app.chat, app.status, app.info_bar)

        row = app.info_bar._format_row1_right().plain
        assert "180k/200k" not in row
        assert app.info_bar._context_tokens is not None
        assert app.info_bar._context_tokens < 180_000

    @pytest.mark.asyncio
    async def test_manual_compact_updates_the_bar_without_a_new_message(
        self, monkeypatch, fake_chat
    ):
        session = _session_awaiting_compaction()
        provider = MockProvider()
        app = _TestCommandsApp(session=session, provider=provider, chat=fake_chat)
        app.info_bar.update_tokens(180_000, 500)
        assert "180k/200k" in app.info_bar._format_row1_right().plain

        async def _fake_summary(*args, **kwargs):
            return "short summary"

        monkeypatch.setattr("vtx.agent.runtime.generate_summary", _fake_summary)

        await app._do_compact()

        row = app.info_bar._format_row1_right().plain
        assert "180k/200k" not in row
        assert app.info_bar._context_tokens is not None
        assert app.info_bar._context_tokens < 180_000

    @pytest.mark.asyncio
    async def test_a_failed_compaction_leaves_the_bar_alone(self, monkeypatch, fake_chat):
        """A failed compaction kept the history, so the old figure still holds."""
        session = _session_awaiting_compaction()
        provider = MockProvider()
        app = _TestCommandsApp(session=session, provider=provider, chat=fake_chat)
        app.info_bar.update_tokens(180_000, 500)

        async def _boom(*args, **kwargs):
            raise RuntimeError("provider 500")

        monkeypatch.setattr("vtx.agent.runtime.generate_summary", _boom)

        await app._do_compact()

        assert "180k/200k" in app.info_bar._format_row1_right().plain

    @pytest.mark.asyncio
    async def test_an_aborted_auto_compaction_leaves_the_bar_alone(self, monkeypatch):
        session = _session_awaiting_compaction()
        app = _TestRunnerApp(session)
        app.info_bar.update_tokens(180_000, 500)

        await app._render_agent_event(
            CompactionEndEvent(tokens_before=180_500, aborted=True, reason="boom"),
            app.chat,
            app.status,
            app.info_bar,
        )

        assert "180k/200k" in app.info_bar._format_row1_right().plain
        assert app.chat.compaction_calls == [(180_500, 0)]


async def _auto_compaction_events(session: Session) -> list[CompactionEndEvent]:
    """Drive the real agent compaction path and collect its events."""
    agent = Agent(
        provider=MockProvider(),
        tools=[],
        session=session,
        system_prompt="system",
        config=AgentConfig(context_window=200_000, max_output_tokens=1),
    )
    return [e async for e in agent._check_compaction(StopReason.STOP, "system", None)]


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestCompactionConfig:
    def test_default_config_values(self):
        cfg = Config({})
        assert cfg.compaction.on_overflow == "continue"
        assert cfg.compaction.threshold_percent == 80.0
        assert cfg.agent.default_context_window == 200000

    def test_config_override(self):
        cfg = Config({"compaction": {"on_overflow": "pause", "threshold_percent": 90.0}})
        assert cfg.compaction.on_overflow == "pause"
        assert cfg.compaction.threshold_percent == 90.0
        assert cfg.agent.default_context_window == 200000

    def test_badge_colors_default(self):
        cfg = Config({})
        assert cfg.ui.colors.info == "#fabd2f"
        assert cfg.ui.colors.notice == "#fe8019"
        assert cfg.ui.colors.badge.bg == "#3c3836"
        assert cfg.ui.colors.badge.label == "#d3869b"

    def test_theme_selection_changes_palette(self):
        cfg = Config({"ui": {"theme": "one-light"}})
        assert cfg.ui.theme == "one-light"
        assert cfg.ui.colors.bg == "#fafafa"
        assert cfg.ui.colors.accent == "#4078f2"


class TestCompactionTokenCalculation:
    def test_token_totals_ignores_pre_compaction_for_context_tokens(self):
        session = Session.in_memory()
        session.append_message(UserMessage(content="hi"))
        session.append_message(
            AssistantMessage(
                content=[TextContent(text="usable")],
                usage=Usage(
                    input_tokens=3000,
                    output_tokens=500,
                    cache_read_tokens=100,
                    cache_write_tokens=50,
                ),
            )
        )

        # Initially, context_tokens should be the max (3650)
        totals = session.token_totals()
        assert totals.context_tokens == 3650

        # Append a compaction entry
        session.append_compaction(
            summary="This is a summary of 30 characters.",
            first_kept_entry_id=session.leaf_id or "",
            tokens_before=3650,
            tokens_after=100,  # Explicitly set
        )

        # token_totals should now return context_tokens estimated or from the new usages
        totals = session.token_totals()
        # With compaction but no new assistant message, it should estimate from session.messages
        # self.messages has UserMessage + AssistantMessage summary
        assert totals.context_tokens < 100

        # Now add an assistant message after compaction with usage
        session.append_message(
            AssistantMessage(
                content=[TextContent(text="post-compact")],
                usage=Usage(
                    input_tokens=500, output_tokens=100, cache_read_tokens=0, cache_write_tokens=0
                ),
            )
        )

        totals = session.token_totals()
        # Since there is a post-compaction message with usage, it should use its usage (600)
        assert totals.context_tokens == 600

        # Cumulative tokens should still include everything
        assert totals.input_tokens == 3500  # 3000 + 500
        assert totals.output_tokens == 600  # 500 + 100


# ---------------------------------------------------------------------------
# Compaction progress events
# ---------------------------------------------------------------------------


class TestCompactionProgressEvents:
    def _agent(self):
        session = Session.in_memory()
        session.append_message(UserMessage(content="hi"))
        session.append_message(
            AssistantMessage(
                content=[TextContent(text="usable")],
                usage=Usage(
                    input_tokens=3000,
                    output_tokens=500,
                    cache_read_tokens=100,
                    cache_write_tokens=50,
                ),
            )
        )
        return Agent(
            provider=MockProvider(),
            tools=[],
            session=session,
            system_prompt="system",
            config=AgentConfig(context_window=1000, max_output_tokens=1),
        )

    @staticmethod
    def _streaming_summary(chunks):
        async def _fake_summary(*args, on_delta=None, **kwargs):
            from vtx.core.compaction import SummaryProgress

            # Cumulative, matching the real generate_summary contract.
            sections: list[tuple[int, str]] = []
            chars = 0
            for chunk in chunks:
                chars += len(chunk)
                if on_delta is not None:
                    if "##" in chunk:
                        sections = [(1, "Objective & Constraints")]
                    on_delta(SummaryProgress(chars=chars, sections_started=list(sections)))
                await asyncio.sleep(0)
            return "final summary"

        return _fake_summary

    @pytest.mark.asyncio
    async def test_start_event_carries_context_and_overflow_trigger(self, monkeypatch):
        monkeypatch.setattr("vtx.agent.loop.generate_summary", self._streaming_summary([]))
        agent = self._agent()
        events = [e async for e in agent._check_compaction(StopReason.STOP, "system", None)]
        start = events[0]
        assert start.type == "compaction_start"
        assert start.tokens_before == 3650
        assert start.context_window == 1000
        assert start.trigger == "overflow"

    @pytest.mark.asyncio
    async def test_end_event_carries_the_summary(self, monkeypatch):
        monkeypatch.setattr("vtx.agent.loop.generate_summary", self._streaming_summary([]))
        agent = self._agent()
        events = [e async for e in agent._check_compaction(StopReason.STOP, "system", None)]
        end = next(e for e in events if e.type == "compaction_end")
        assert end.summary == "final summary"
        assert end.aborted is False

    @pytest.mark.asyncio
    async def test_progress_events_are_emitted_and_precede_the_end(self, monkeypatch):
        monkeypatch.setattr(
            "vtx.agent.loop.generate_summary",
            self._streaming_summary(["<summary>\n## 1. Objective", " & Constraints\nbody"]),
        )
        agent = self._agent()
        events = [e async for e in agent._check_compaction(StopReason.STOP, "system", None)]
        types = [e.type for e in events]
        assert types[0] == "compaction_start"
        assert types[-1] == "compaction_end"
        assert "compaction_progress" in types
        assert types.index("compaction_progress") < types.index("compaction_end")

    @pytest.mark.asyncio
    async def test_progress_sections_carry_the_started_headings(self, monkeypatch):
        monkeypatch.setattr(
            "vtx.agent.loop.generate_summary",
            self._streaming_summary(["<summary>\n## 1. Objective", " & Constraints\nbody"]),
        )
        agent = self._agent()
        events = [e async for e in agent._check_compaction(StopReason.STOP, "system", None)]
        progress = [e for e in events if e.type == "compaction_progress"]
        assert progress
        assert progress[-1].sections_started == [(1, "Objective & Constraints")]
        assert progress[-1].chars > 0

    @pytest.mark.asyncio
    async def test_summary_failure_still_yields_an_aborted_end_event(self, monkeypatch):
        async def _boom(*args, **kwargs):
            raise RuntimeError("provider exploded")

        monkeypatch.setattr("vtx.agent.loop.generate_summary", _boom)
        agent = self._agent()
        events = [e async for e in agent._check_compaction(StopReason.STOP, "system", None)]
        end = next(e for e in events if e.type == "compaction_end")
        assert end.aborted is True
        assert "provider exploded" in end.reason

    @pytest.mark.asyncio
    async def test_no_progress_queue_leak_between_runs(self, monkeypatch):
        monkeypatch.setattr("vtx.agent.loop.generate_summary", self._streaming_summary([]))
        agent = self._agent()
        [e async for e in agent._check_compaction(StopReason.STOP, "system", None)]
        assert len(agent._compaction_progress) == 0
