"""Compaction: openclaude-style budget, tool pruning and preserved recent tail."""

import asyncio

from ctx.agent.session import Session
from ctx.core.compaction import (
    CLEARED_TOOL_RESULT,
    estimate_messages_tokens,
    generate_summary,
    prepare_summary_messages,
    rough_token_count,
    select_preserved_tail,
)
from ctx.core.harness_config import get_harness_config
from ctx.protocol.types import ImageContent, TextContent, TextPart, ToolResultMessage, UserMessage


def user(text: str) -> UserMessage:
    return UserMessage(content=text)


def result(index: int, size: int = 200) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=str(index), tool_name="read", content=[TextContent(text="x" * size)]
    )


class TestConfig:
    def test_knobs_are_wired(self):
        cfg = get_harness_config()
        assert cfg.compaction_summary_max_tokens == 20_000
        assert cfg.compaction_keep_recent_tokens == 12_000
        assert cfg.compaction_keep_max_tokens == 40_000
        assert cfg.compaction_prune_tool_results is True


class TestHelpers:
    def test_rough_tokens(self):
        assert rough_token_count("abcd" * 4) == 4

    def test_estimate_messages(self):
        assert estimate_messages_tokens([user("abcd" * 8)]) >= 8

    def test_prepare_clears_old_results_but_keeps_last_three(self):
        messages = [result(i) for i in range(5)]
        prepared = prepare_summary_messages(messages, tool_results_to_keep=3)
        cleared = [
            m
            for m in prepared
            if isinstance(m.content[0], TextContent) and m.content[0].text == CLEARED_TOOL_RESULT
        ]
        assert len(cleared) == 2
        assert prepared[-1].content[0].text == "x" * 200

    def test_prepare_strips_images(self):
        message = UserMessage(
            content=[TextContent(text="look"), ImageContent(data="abc", mime_type="image/png")]
        )
        prepared = prepare_summary_messages([message], prune_tool_results=False)
        assert not any(isinstance(p, ImageContent) for p in prepared[0].content)
        assert any(isinstance(p, TextContent) and p.text == "[image]" for p in prepared[0].content)

    def test_tail_meets_minimums_and_avoids_orphan_tool_result(self):
        messages = [result(0), user("one"), result(1), user("two"), user("three"), user("four")]
        tail = select_preserved_tail(
            messages, min_tokens=1, max_tokens=10_000, min_text_messages=3
        )
        assert len(tail) >= 3
        assert not isinstance(tail[0], ToolResultMessage)


class TestSessionPreservedTail:
    def test_messages_include_preserved_tail(self):
        session = Session("s1", "/tmp", persist=False)
        first = session.append_message(user("old"))
        tail_ids = [session.append_message(user(f"recent {i}")) for i in range(5)]
        session.append_message(user("last"))
        preserved_from, _tail, tokens = session.select_preservation_tail(
            min_tokens=1, max_tokens=10_000, min_text_messages=2
        )
        assert preserved_from is not None
        assert tokens >= 1

        session.append_compaction(
            summary="SUMMARY",
            first_kept_entry_id=session.leaf_id or "",
            tokens_before=100,
            tokens_after=10,
            preserved_from_id=preserved_from,
            preserved_tokens=tokens,
        )
        texts = []
        for message in session.messages:
            content = message.content
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                texts.extend(p.text for p in content if isinstance(p, TextContent))
        assert "SUMMARY" in texts
        assert any("recent" in t for t in texts)
        assert first != tail_ids[0]


class _FakeProvider:
    def __init__(self):
        self.max_tokens = None

    async def stream(self, messages, **kwargs):
        self.max_tokens = kwargs.get("max_tokens")

        async def gen():
            yield TextPart(text="ok")

        return gen()


class TestSummaryBudget:
    def test_max_tokens_reaches_provider(self):
        provider = _FakeProvider()
        out = asyncio.run(generate_summary([user("hi")], provider, max_tokens=17_000))
        assert out == "ok"
        assert provider.max_tokens == 17_000
