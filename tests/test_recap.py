import pytest

from vtx.ai.providers.mock import MockProvider
from vtx.coding_agent.config import Config
from vtx.core.recap import (
    RECENT_MESSAGE_WINDOW,
    TOOL_RESULT_EDGE_CHARS,
    build_recap_context,
    generate_recap,
    has_meaningful_activity,
)
from vtx.core.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text=text)])


def _tool_result(call_id: str, text: str) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call_id, tool_name="read", content=[TextContent(text=text)]
    )


class TestBuildRecapContext:
    def test_short_conversation_passes_through(self):
        messages = [UserMessage(content="hello"), _assistant("hi")]
        context = build_recap_context(messages)
        assert len(context.messages) == 2
        assert context.broader_context is None

    def test_window_keeps_recent_messages(self):
        messages = [UserMessage(content=f"msg {i}") for i in range(RECENT_MESSAGE_WINDOW + 10)]
        context = build_recap_context(messages)
        assert len(context.messages) == RECENT_MESSAGE_WINDOW

    def test_window_does_not_start_on_tool_result(self):
        # Build a conversation whose window boundary would land on a tool result.
        block = []
        for i in range(15):
            block.append(UserMessage(content=f"q{i}"))
            block.append(_assistant(f"a{i}"))
            block.append(_tool_result(f"t{i}", "output"))
        messages = [*block, UserMessage(content="final")]
        context = build_recap_context(messages)
        assert not isinstance(context.messages[0], ToolResultMessage)

    def test_prepends_placeholder_when_first_is_assistant(self):
        messages = [_assistant("answer"), UserMessage(content="next")]
        context = build_recap_context(messages)
        assert isinstance(context.messages[0], UserMessage)
        assert "omitted" in context.messages[0].content  # type: ignore[union-attr]

    def test_long_tool_result_truncated(self):
        long_text = "x" * (TOOL_RESULT_EDGE_CHARS * 3)
        messages = [UserMessage(content="read it"), _tool_result("t1", long_text)]
        context = build_recap_context(messages)
        result = context.messages[1]
        assert isinstance(result, ToolResultMessage)
        text = result.content[0].text
        assert "[tool result truncated for recap]" in text
        assert len(text) < len(long_text)

    def test_initial_task_included_when_not_in_recent(self):
        recent = [UserMessage(content="and then?"), _assistant("ok")]
        context = build_recap_context(recent, initial_task="build the parser")
        assert context.broader_context is not None
        assert "Initial user request" in context.broader_context
        assert "build the parser" in context.broader_context

    def test_initial_task_not_duplicated_when_recent(self):
        recent = [UserMessage(content="build the parser")]
        context = build_recap_context(recent, initial_task="build the parser")
        assert context.broader_context is None

    def test_compaction_summary_included(self):
        context = build_recap_context([], compaction_summary="We were fixing auth.")
        assert context.broader_context == "Session summary:\nWe were fixing auth."


class TestHasMeaningfulActivity:
    def test_three_tool_calls_count_as_activity(self):
        messages = [
            UserMessage(content="go"),
            AssistantMessage(
                content=[
                    ToolCall(id="t1", name="bash", arguments={"command": "ls"}),
                    ToolCall(id="t2", name="read", arguments={"path": "x"}),
                    ToolCall(id="t3", name="grep", arguments={"pattern": "y"}),
                ]
            ),
        ]
        assert has_meaningful_activity(messages)

    def test_three_tool_results_count_as_activity(self):
        messages = [
            UserMessage(content="go"),
            _tool_result("t1", "out1"),
            _tool_result("t2", "out2"),
            _tool_result("t3", "out3"),
        ]
        assert has_meaningful_activity(messages)

    def test_one_tool_call_is_not_enough(self):
        messages = [
            UserMessage(content="go"),
            AssistantMessage(
                content=[ToolCall(id="t1", name="bash", arguments={"command": "ls"})]
            ),
        ]
        assert not has_meaningful_activity(messages)

    def test_two_tool_calls_are_not_enough(self):
        messages = [
            UserMessage(content="go"),
            AssistantMessage(
                content=[
                    ToolCall(id="t1", name="bash", arguments={"command": "ls"}),
                    ToolCall(id="t2", name="read", arguments={"path": "x"}),
                ]
            ),
        ]
        assert not has_meaningful_activity(messages)

    def test_short_assistant_text_is_activity(self):
        messages = [UserMessage(content="go"), _assistant("done")]
        assert has_meaningful_activity(messages)

    def test_empty_assistant_reply_is_not_activity(self):
        messages = [UserMessage(content="go"), AssistantMessage(content=[])]
        assert not has_meaningful_activity(messages)

    def test_long_assistant_text_is_activity(self):
        messages = [UserMessage(content="go"), _assistant("word " * 35)]
        assert has_meaningful_activity(messages)

    def test_no_tail_after_last_user_message(self):
        messages = [_assistant("word " * 50), UserMessage(content="second")]
        assert not has_meaningful_activity(messages)

    def test_activity_only_counts_after_last_user_message(self):
        messages = [
            UserMessage(content="first"),
            _assistant("word " * 50),
            UserMessage(content="second"),
            AssistantMessage(content=[]),
        ]
        assert not has_meaningful_activity(messages)

    def test_empty_messages(self):
        assert not has_meaningful_activity([])


class TestGenerateRecap:
    @pytest.mark.asyncio
    async def test_generates_text_with_prompt_appended(self):
        provider = MockProvider(scenario="simple_text")
        messages = [UserMessage(content="hello"), _assistant("hi there")]
        recap = await generate_recap(build_recap_context(messages), provider)
        assert recap == "Hello, world!"
        last = provider._last_messages[-1]
        assert isinstance(last, UserMessage)
        assert "stepped away" in last.content

    @pytest.mark.asyncio
    async def test_broader_context_prepended_to_prompt(self):
        provider = MockProvider(scenario="simple_text")
        context = build_recap_context(
            [UserMessage(content="hi")],
            initial_task="refactor the importer",
            compaction_summary="earlier work",
        )
        await generate_recap(context, provider)
        prompt = provider._last_messages[-1]
        assert isinstance(prompt, UserMessage)
        assert "Initial user request" in prompt.content
        assert "Session summary" in prompt.content

    @pytest.mark.asyncio
    async def test_error_propagates(self):
        provider = MockProvider(scenario="retry_exhausted")
        with pytest.raises(ConnectionError):
            await generate_recap(build_recap_context([UserMessage(content="x")]), provider)


class TestRecapConfig:
    def test_defaults(self):
        cfg = Config({})
        assert cfg.recap.enabled is True
        assert cfg.recap.idle_seconds == 30

    def test_override(self):
        cfg = Config({"recap": {"enabled": False, "idle_seconds": 60}})
        assert cfg.recap.enabled is False
        assert cfg.recap.idle_seconds == 60

    def test_idle_seconds_minimum_enforced(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Config({"recap": {"idle_seconds": 1}})
