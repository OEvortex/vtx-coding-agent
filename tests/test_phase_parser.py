"""Tests for the real-time thinking phase parser and its integration
with the OpenAI SDK provider.

Covers the state machine (idle → thinking → responding), real-time
event emission (no post-hoc buffering), tag-split-across-chunks
robustness, the orphan-think-block recovery on flush, and the
multi-turn round-trip from provider → ThinkingContent → wire format.
"""

from __future__ import annotations

from vtx.core.types import AssistantMessage, TextContent, ThinkingContent
from vtx.llm.base import ProviderConfig
from vtx.llm.phase_parser import (
    INLINE_THINK_SIGNATURE,
    ResponseDelta,
    ResponseEnd,
    ResponseStart,
    ThinkDelta,
    ThinkEnd,
    ThinkingPhaseParser,
    ThinkStart,
)
from vtx.llm.providers.openai_sdk import OpenAISDKProvider
from vtx.llm.sdk.openai import _openai_stream_chunks

# =================================================================================================
# Test helpers
# =================================================================================================


def _types(events) -> list[str]:
    """Return just the class names of a list of events (for compact assertions)."""
    return [type(e).__name__ for e in events]


def _phases(events) -> list[tuple[str, str]]:
    """Return ``[(class_name, payload), ...]`` for assertion-friendly output."""
    out: list[tuple[str, str]] = []
    for e in events:
        if isinstance(e, ThinkStart):
            out.append(("ThinkStart", ""))
        elif isinstance(e, ThinkDelta):
            out.append(("ThinkDelta", e.text))
        elif isinstance(e, ThinkEnd):
            out.append(("ThinkEnd", e.full_thinking))
        elif isinstance(e, ResponseStart):
            out.append(("ResponseStart", ""))
        elif isinstance(e, ResponseDelta):
            out.append(("ResponseDelta", e.text))
        elif isinstance(e, ResponseEnd):
            out.append(("ResponseEnd", ""))
    return out


def _collect(events) -> tuple[str, str]:
    """Concatenate all thinking and response text from a sequence of events."""
    thinking = "".join(
        e.text if isinstance(e, ThinkDelta) else e.full_thinking
        for e in events
        if isinstance(e, (ThinkDelta, ThinkEnd))
    )
    response = "".join(e.text for e in events if isinstance(e, ResponseDelta))
    return thinking, response


# =================================================================================================
# Phase events: state machine + real-time emission
# =================================================================================================


def test_phase_parser_no_think_block_long_text() -> None:
    p = ThinkingPhaseParser()
    text = "this is a fairly long response that has no think tags at all"
    events = list(p.feed(text))
    events.extend(p.flush())
    # No ThinkStart, ThinkDelta, or ThinkEnd.
    assert not any(isinstance(e, (ThinkStart, ThinkDelta, ThinkEnd)) for e in events)
    # The full text round-trips as response.
    _, response = _collect(events)
    assert response == text
    # Exactly one ResponseStart at the start and one ResponseEnd at the end.
    assert any(isinstance(e, ResponseStart) for e in events)
    assert any(isinstance(e, ResponseEnd) for e in events)


def test_phase_parser_full_think_block() -> None:
    p = ThinkingPhaseParser()
    events = list(
        p.feed(
            "<think>this is the reasoning behind my answer</think>"
            "and this is the actual response to the user"
        )
    )
    events.extend(p.flush())
    thinking, response = _collect(events)
    assert thinking == "this is the reasoning behind my answer"
    assert response == "and this is the actual response to the user"
    # Phase boundaries: ThinkStart, ThinkEnd, ResponseStart, ResponseEnd all present.
    assert any(isinstance(e, ThinkStart) for e in events)
    assert any(isinstance(e, ThinkEnd) for e in events)
    assert any(isinstance(e, ResponseStart) for e in events)
    assert any(isinstance(e, ResponseEnd) for e in events)


def test_phase_parser_text_around_think() -> None:
    p = ThinkingPhaseParser()
    events = list(p.feed("before <think>think</think> after response text"))
    events.extend(p.flush())
    thinking, response = _collect(events)
    assert thinking == "think"
    assert response == "before " + " after response text"
    names = _types(events)
    assert names[0] == "ResponseStart"
    assert "ThinkStart" in names
    assert "ThinkEnd" in names
    assert names[-1] == "ResponseEnd"


def test_phase_parser_multiple_think_blocks() -> None:
    """Some models interleave thinking — emit the right events for each block."""
    p = ThinkingPhaseParser()
    events = list(p.feed("a<think>first reasoning</think>b<think>second reasoning</think>c"))
    events.extend(p.flush())
    thinking, response = _collect(events)
    assert thinking == "first reasoningsecond reasoning"
    assert response == "abc"
    # Two think blocks.
    assert _types(events).count("ThinkStart") == 2
    assert _types(events).count("ThinkEnd") == 2


# =================================================================================================
# Real-time: deltas must be emitted eagerly
# =================================================================================================


def test_phase_parser_emits_deltas_eagerly_during_thinking() -> None:
    """A long thinking stream should emit ThinkDeltas as the chunks
    arrive, not wait for the close tag."""
    p = ThinkingPhaseParser()
    events_1 = list(p.feed("<think>first chunk of thinking is here"))
    # No ThinkEnd yet (we haven't seen the close tag).
    assert any(isinstance(e, ThinkDelta) for e in events_1)
    assert not any(isinstance(e, ThinkEnd) for e in events_1)
    # Feed more chunks — more deltas emitted.
    events_2 = list(p.feed(" second chunk of thinking"))
    assert any(isinstance(e, ThinkDelta) for e in events_2)


# =================================================================================================
# Edge case: tags split across chunks
# =================================================================================================


def test_phase_parser_opener_split_across_chunks() -> None:
    p = ThinkingPhaseParser()
    assert _phases(p.feed("<thi")) == []
    assert _phases(p.feed("nk>reasoning behind my answer</think>")) == [("ThinkStart", "")]
    assert _phases(p.feed("response text here")) == [
        ("ThinkDelta", "reasoning behind my answer"),
        ("ThinkEnd", "reasoning behind my answer"),
        ("ResponseStart", ""),
        ("ResponseDelta", "response text here"),
    ]
    events = list(p.flush())
    assert _phases(events) == [("ResponseEnd", "")]


def test_phase_parser_closer_split_across_chunks() -> None:
    p = ThinkingPhaseParser()
    assert _phases(p.feed("<think>reasoning behind my answer</think")) == [
        ("ThinkStart", ""),
        ("ThinkDelta", "reasoning behind my answer"),
    ]
    assert _phases(p.feed(">response text here")) == [
        ("ThinkEnd", "reasoning behind my answer"),
        ("ResponseStart", ""),
        ("ResponseDelta", "response text here"),
    ]


# =================================================================================================
# Edge case: unterminated think block
# =================================================================================================


def test_phase_parser_orphan_think_block() -> None:
    """Model ran out of tokens mid-thinking — flush() should still
    surface the partial reasoning so the user sees it."""
    p = ThinkingPhaseParser()
    assert _phases(p.feed("<think>orphan reasoning here")) == [
        ("ThinkStart", ""),
        ("ThinkDelta", "orphan reasoning here"),
    ]
    assert _phases(p.flush()) == [("ThinkEnd", "orphan reasoning here")]


def test_phase_parser_trailing_response() -> None:
    """Plain response with text held back in the buffer — should emit a
    final ResponseDelta and ResponseEnd on flush."""
    p = ThinkingPhaseParser()
    # Response text is emitted eagerly during feed(); flush() closes the stream.
    feed_events = list(p.feed("hello world, this is a long response"))
    assert _phases(feed_events)[0] == ("ResponseStart", "")
    flush_events = list(p.flush())
    all_events = feed_events + flush_events
    _, response = _collect(all_events)
    assert response.endswith("response")
    assert any(isinstance(e, ResponseEnd) for e in flush_events)


# =================================================================================================
# Edge case: the word "think" in normal text doesn't trigger a phase change
# =================================================================================================


def test_phase_parser_partial_tag_does_not_trigger() -> None:
    """The literal word 'think' in normal text must not be misinterpreted
    as the start of a think block."""
    p = ThinkingPhaseParser()
    text = "this is a long sentence that happens to contain the word think inside it"
    events = list(p.feed(text))
    events.extend(p.flush())
    assert not any(isinstance(e, (ThinkStart, ThinkDelta, ThinkEnd)) for e in events)
    _, response = _collect(events)
    assert response == text


# =================================================================================================
# SDK integration: `` blocks must be extracted from delta.content
# =================================================================================================


async def _collect_chunks(stream) -> list[dict]:
    out: list[dict] = []
    async for chunk in stream:
        out.append(chunk)
    return out


def test_sdk_stream_chunks_extracts_inline_thinking() -> None:
    """`` blocks embedded in ``delta.content`` (DeepSeek R1,
    MiniMax M3, Qwen3, GLM) must be split out as ``reasoning``
    chunks so the TUI can render them in a ThinkingBlock instead of
    the response being eaten by the markdown renderer."""
    from openai.types.chat import ChatCompletionChunk
    from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta

    async def _fake_openai_stream():
        yield ChatCompletionChunk(
            id="x",
            object="chat.completion.chunk",
            created=1,
            model="m",
            choices=[
                Choice(
                    index=0,
                    delta=ChoiceDelta(role="assistant", content="<think>\nI need to think"),
                )
            ],
        )
        yield ChatCompletionChunk(
            id="x",
            object="chat.completion.chunk",
            created=1,
            model="m",
            choices=[
                Choice(index=0, delta=ChoiceDelta(content=" about this.\n</think>\nHi there!"))
            ],
        )
        yield ChatCompletionChunk(
            id="x", object="chat.completion.chunk", created=1, model="m", choices=[]
        )

    import asyncio

    chunks = asyncio.run(_collect_chunks(_openai_stream_chunks(_fake_openai_stream())))

    # The reasoning chunk comes from the inline `` block.
    reasoning_chunks = [c for c in chunks if c["type"] == "reasoning"]
    text_chunks = [c for c in chunks if c["type"] == "text"]
    assert len(reasoning_chunks) >= 1
    full_thinking = "".join(c["content"] for c in reasoning_chunks)
    assert "I need to think about this." in full_thinking
    # Inline thinking carries the INLINE_THINK_SIGNATURE so the
    # assistant-message converter knows to inline it back into content
    # on the next turn.
    assert any(c.get("signature") == INLINE_THINK_SIGNATURE for c in reasoning_chunks)
    # The response text is extracted separately and contains no tags.
    full_text = "".join(c["content"] for c in text_chunks)
    assert full_text == "Hi there!"
    assert "<think>" not in full_text
    assert "</think>" not in full_text


def test_sdk_stream_chunks_uses_explicit_reasoning_field() -> None:
    """OpenAI o-series / Anthropic-style ``reasoning_content`` field
    is emitted as a reasoning chunk with the field name as signature."""
    from openai.types.chat import ChatCompletionChunk
    from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta

    async def _fake_openai_stream():
        delta = ChoiceDelta.model_construct(role="assistant", reasoning_content="thinking here")
        yield ChatCompletionChunk(
            id="x",
            object="chat.completion.chunk",
            created=1,
            model="m",
            choices=[Choice(index=0, delta=delta)],
        )
        yield ChatCompletionChunk(
            id="x",
            object="chat.completion.chunk",
            created=1,
            model="m",
            choices=[Choice(index=0, delta=ChoiceDelta(content="the response"))],
        )

    import asyncio

    chunks = asyncio.run(_collect_chunks(_openai_stream_chunks(_fake_openai_stream())))
    reasoning = [c for c in chunks if c["type"] == "reasoning"]
    text = [c for c in chunks if c["type"] == "text"]
    assert len(reasoning) == 1
    assert reasoning[0]["content"] == "thinking here"
    assert reasoning[0]["signature"] == "reasoning_content"
    assert "".join(c["content"] for c in text) == "the response"


# =================================================================================================
# Multi-turn round-trip: inline thinking must be inlined back into content
# =================================================================================================


def test_convert_assistant_message_inlines_inline_thinking_into_content() -> None:
    """The fix for the multi-turn thinking-loss bug: thinking extracted
    from inline `` tags must be re-inlined into the assistant
    content with `` tags on the next turn, so the gateway
    (which has no dedicated reasoning_content field) actually
    receives the chain-of-thought.
    """
    provider = OpenAISDKProvider(
        ProviderConfig(
            api_key="test-key", base_url="https://api.tokenrouter.com/v1", model="MiniMax-M3"
        )
    )
    msg = AssistantMessage(
        content=[
            ThinkingContent(
                thinking="the user asked hi, I should say hi back",
                signature=INLINE_THINK_SIGNATURE,
            ),
            TextContent(text="Hi there!"),
        ]
    )
    result = provider._convert_assistant_message(msg)
    # The thinking must be inlined into content with `` tags, NOT
    # sent as a separate field (which the gateway would drop).
    content = result.content
    assert "<think>" in content
    assert "</think>" in content
    assert "the user asked hi" in content
    assert "Hi there!" in content
    # Crucially: no unknown thinking field on the wire.
    assert result.metadata is None or "reasoning_content" not in result.metadata
    assert result.metadata is None or "_inline" not in result.metadata
    assert result.metadata is None or "<think>_inline" not in result.metadata


def test_convert_assistant_message_echoes_standard_reasoning_field() -> None:
    """Models that expose a real ``reasoning_content`` field (OpenAI
    o-series, Anthropic, etc.) get their thinking echoed back in the
    message metadata so the SDK spreads it into the request body."""
    provider = OpenAISDKProvider(
        ProviderConfig(api_key="test-key", base_url="https://api.openai.com/v1", model="o3-mini")
    )
    msg = AssistantMessage(
        content=[
            ThinkingContent(
                thinking="the user asked for prime numbers", signature="reasoning_content"
            ),
            TextContent(text="2, 3, 5, 7, 11"),
        ]
    )
    result = provider._convert_assistant_message(msg)
    # Standard reasoning field is echoed in metadata.
    assert result.metadata is not None
    assert result.metadata.get("reasoning_content") == "the user asked for prime numbers"
    # And is NOT inlined into content.
    assert "<think>" not in result.content
    # The response text is in content.
    assert "2, 3, 5, 7, 11" in result.content


def test_convert_assistant_message_no_text_with_inline_thinking() -> None:
    """If there's only thinking and no text, the inline think block
    becomes the assistant content on its own."""
    provider = OpenAISDKProvider(
        ProviderConfig(
            api_key="test-key", base_url="https://api.tokenrouter.com/v1", model="MiniMax-M3"
        )
    )
    msg = AssistantMessage(
        content=[
            ThinkingContent(
                thinking="just thinking, no response", signature=INLINE_THINK_SIGNATURE
            )
        ]
    )
    result = provider._convert_assistant_message(msg)
    assert "<think>" in result.content
    assert "</think>" in result.content


# =================================================================================================
# End-to-end: SDK stream → SDK chunks → provider parts → round-trip
# =================================================================================================


def test_streamed_inline_thinking_round_trips_through_sdk() -> None:
    """End-to-end: a streamed ``-wrapped response from tokenrouter
    gets split into reasoning + text chunks by the SDK, then on the
    next turn the converter inlines the thinking back into content
    for the API."""
    from openai.types.chat import ChatCompletionChunk
    from openai.types.chat.chat_completion_chunk import Choice, ChoiceDelta

    async def _fake_openai_stream():
        yield ChatCompletionChunk(
            id="x",
            object="chat.completion.chunk",
            created=1,
            model="m",
            choices=[
                Choice(
                    index=0, delta=ChoiceDelta(role="assistant", content="<think>\nLet me think")
                )
            ],
        )
        yield ChatCompletionChunk(
            id="x",
            object="chat.completion.chunk",
            created=1,
            model="m",
            choices=[
                Choice(index=0, delta=ChoiceDelta(content=" about this.\n</think>\nHi there!"))
            ],
        )
        yield ChatCompletionChunk(
            id="x", object="chat.completion.chunk", created=1, model="m", choices=[]
        )

    import asyncio

    chunks = asyncio.run(_collect_chunks(_openai_stream_chunks(_fake_openai_stream())))
    full_thinking = "".join(c["content"] for c in chunks if c["type"] == "reasoning")
    full_text = "".join(c["content"] for c in chunks if c["type"] == "text")
    assert full_text == "Hi there!"
    assert "Let me think about this." in full_thinking

    # Round-trip: build AssistantMessage, convert back, verify the
    # thinking is inlined into content.
    provider = OpenAISDKProvider(
        ProviderConfig(
            api_key="test-key", base_url="https://api.tokenrouter.com/v1", model="MiniMax-M3"
        )
    )
    # Use the first reasoning chunk's signature.
    first_sig = next(
        (c.get("signature") for c in chunks if c["type"] == "reasoning"), INLINE_THINK_SIGNATURE
    )
    msg = AssistantMessage(
        content=[
            ThinkingContent(thinking=full_thinking, signature=first_sig),
            TextContent(text=full_text),
        ]
    )
    converted = provider._convert_assistant_message(msg)
    assert "<think>" in converted.content
    assert "Let me think about this." in converted.content
    assert "Hi there!" in converted.content
