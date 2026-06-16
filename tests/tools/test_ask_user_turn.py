"""End-to-end test of the ask_user tool through run_single_turn.

These tests exercise the interception logic in ``_TurnRunner._run_ask_user``:
the turn runner should yield an ``AskUserEvent`` for the UI to resolve,
then build a tool result message from the response and emit a normal
``ToolResultEvent``.
"""

import asyncio
import json
from collections.abc import AsyncIterator

import pytest

from vtx.core.types import (
    Message,
    StopReason,
    StreamDone,
    StreamPart,
    TextPart,
    ToolCallDelta,
    ToolCallStart,
    ToolDefinition,
    UserMessage,
)
from vtx.events import AskUserEvent, ToolResultEvent, TurnEndEvent
from vtx.llm.base import BaseProvider, LLMStream, ProviderConfig
from vtx.permissions import AskUserResponse
from vtx.tools.ask_user import AskUserTool
from vtx.tools.base import BaseTool
from vtx.turn import run_single_turn


class _ScriptedProvider(BaseProvider):
    """Emits a fixed stream of parts regardless of input."""

    name = "scripted"

    def __init__(self, parts: list[StreamPart], config: ProviderConfig | None = None):
        super().__init__(config or ProviderConfig(model="scripted"))
        self._parts = parts

    async def _stream_impl(
        self,
        messages: list[Message],
        *,
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMStream:
        async def iterator() -> AsyncIterator[StreamPart]:
            for part in self._parts:
                yield part

        stream = LLMStream()
        stream.set_iterator(iterator())
        return stream

    def should_retry_for_error(self, error: Exception) -> bool:
        return False


def _ask_user_tool_call(call_id: str, args: dict) -> list[StreamPart]:
    return [
        TextPart(text="I have a question."),
        ToolCallStart(id=call_id, name="ask_user", index=0, arguments={}),
        ToolCallDelta(index=0, arguments_delta=json.dumps(args)),
        StreamDone(stop_reason=StopReason.TOOL_USE),
    ]


def _drain(events: AsyncIterator) -> list:
    out: list = []

    async def collect():
        async for ev in events:
            out.append(ev)

    asyncio.run(collect())
    return out


@pytest.mark.asyncio
async def test_ask_user_with_options_yields_event_and_records_selection():
    ask_user_tool = AskUserTool()
    parts = _ask_user_tool_call(
        "call-1",
        {
            "question": "Pick a package manager",
            "options": [
                {"label": "npm", "description": "default"},
                {"label": "pnpm", "description": "fast"},
            ],
            "multi_select": False,
            "header": "Pkg mgr",
        },
    )
    provider = _ScriptedProvider(parts)
    messages: list[Message] = [UserMessage(content="Which one?")]
    tools: list[BaseTool] = [ask_user_tool]

    events: list = []

    async def consumer():
        async for ev in run_single_turn(provider, messages, tools, turn=1):
            events.append(ev)
            if isinstance(ev, AskUserEvent):
                # Simulate the UI: resolve the future with a selection.
                assert ev.future is not None
                ev.future.set_result(AskUserResponse(selections=("pnpm",)))

    await consumer()

    # AskUserEvent should be present
    ask_events = [e for e in events if isinstance(e, AskUserEvent)]
    assert len(ask_events) == 1
    ask = ask_events[0]
    assert ask.question == "Pick a package manager"
    assert ask.multi_select is False
    assert ask.header == "Pkg mgr"
    assert [o.label for o in ask.options] == ["npm", "pnpm"]

    # ToolResultEvent should carry the user's answer
    result_events = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(result_events) == 1
    r = result_events[0]
    assert r.tool_name == "ask_user"
    assert r.result is not None
    assert any("pnpm" in c.text for c in r.result.content)
    assert r.result.ui_summary and "pnpm" in r.result.ui_summary

    # Turn ends cleanly
    turn_end = next(e for e in events if isinstance(e, TurnEndEvent))
    assert turn_end.stop_reason == StopReason.TOOL_USE
    assert any("pnpm" in c.text for tr in turn_end.tool_results for c in tr.content)


@pytest.mark.asyncio
async def test_ask_user_with_custom_text_response():
    ask_user_tool = AskUserTool()
    parts = _ask_user_tool_call(
        "call-1",
        {"question": "What is your favorite color?"},  # open-ended
    )
    provider = _ScriptedProvider(parts)
    messages: list[Message] = [UserMessage(content="color")]
    tools: list[BaseTool] = [ask_user_tool]

    events: list = []

    async def consumer():
        async for ev in run_single_turn(provider, messages, tools, turn=1):
            events.append(ev)
            if isinstance(ev, AskUserEvent):
                assert ev.future is not None
                ev.future.set_result(AskUserResponse(custom_text="blue"))

    await consumer()

    result_events = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(result_events) == 1
    r = result_events[0]
    assert any("blue" in c.text for c in r.result.content)
    assert "custom" in r.result.content[0].text.lower()


@pytest.mark.asyncio
async def test_ask_user_cancelled_produces_skipped_result():
    ask_user_tool = AskUserTool()
    parts = _ask_user_tool_call("call-1", {"question": "Pick one"})
    provider = _ScriptedProvider(parts)
    messages: list[Message] = [UserMessage(content="?")]
    tools: list[BaseTool] = [ask_user_tool]

    events: list = []

    async def consumer():
        async for ev in run_single_turn(provider, messages, tools, turn=1):
            events.append(ev)
            if isinstance(ev, AskUserEvent):
                # Simulate the user pressing Escape: empty response
                assert ev.future is not None
                ev.future.set_result(AskUserResponse())

    await consumer()

    result_events = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(result_events) == 1
    r = result_events[0]
    # Skipped result is a "tool error" so the LLM can see the hint.
    assert r.result.is_error is True
    assert any("did not" in c.text.lower() for c in r.result.content)


@pytest.mark.asyncio
async def test_ask_user_invalid_args_yields_error_result():
    """A bad tool call (e.g. only one option) shouldn't crash the turn.

    The preflight validator in ``_finalize_tool_call_data`` catches
    schema errors before our ``_run_ask_user`` interception runs, so
    the turn runner surfaces the standard "skipped" result.
    """
    ask_user_tool = AskUserTool()
    parts = _ask_user_tool_call(
        "call-1",
        {
            "question": "Pick one",
            "options": [{"label": "only"}],  # 1 option: invalid
        },
    )
    provider = _ScriptedProvider(parts)
    messages: list[Message] = [UserMessage(content="?")]
    tools: list[BaseTool] = [ask_user_tool]

    events: list = []

    async def consumer():
        async for ev in run_single_turn(provider, messages, tools, turn=1):
            events.append(ev)

    await consumer()

    # No AskUserEvent should be emitted; the invalid args become a tool
    # error result.
    assert not any(isinstance(e, AskUserEvent) for e in events)
    result_events = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(result_events) == 1
    r = result_events[0]
    # The preflight validator flags the bad args as a tool error and
    # skips execution. The exact message is generic on purpose; the
    # LLM should be able to figure out the schema error from context.
    assert r.result.is_error is True
    assert len(r.result.content) == 1


@pytest.mark.asyncio
async def test_ask_user_cancelled_mid_prompt():
    """If the cancel event fires while the user is being asked, the tool
    call is treated as cancelled (no error, no answer)."""
    ask_user_tool = AskUserTool()
    parts = _ask_user_tool_call("call-1", {"question": "Pick one"})
    provider = _ScriptedProvider(parts)
    messages: list[Message] = [UserMessage(content="?")]
    tools: list[BaseTool] = [ask_user_tool]

    cancel = asyncio.Event()
    events: list = []

    async def consumer():
        async for ev in run_single_turn(provider, messages, tools, turn=1, cancel_event=cancel):
            events.append(ev)
            if isinstance(ev, AskUserEvent):
                # Simulate user pressing Ctrl-C: fire cancel before the
                # future is resolved.
                cancel.set()
                # Give the event loop a chance to cancel the await.
                await asyncio.sleep(0)

    await consumer()

    # The future never resolves, but the cancel event triggers
    # OperationCancelledError and the result is a skipped tool call.
    result_events = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(result_events) == 1
    r = result_events[0]
    # Skipped results are always flagged as errors so the LLM sees the
    # hint text. Verify the message tells the model the user didn't
    # answer so it can recover gracefully.
    assert r.result.is_error is True
    assert any("did not answer" in c.text.lower() for c in r.result.content)
