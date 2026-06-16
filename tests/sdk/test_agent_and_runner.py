"""Tests for the Agent wrapper and the Runner's basic flow."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from vtx.sdk import Agent, Runner, function_tool
from vtx.sdk.run_config import RunConfig
from vtx.sdk.sessions import InMemorySession


def test_agent_required_name() -> None:
    with pytest.raises(ValueError):
        Agent(name="")
    with pytest.raises(ValueError):
        bad_name: Any = 123
        Agent(name=bad_name)  # type: ignore[arg-type]


def test_agent_post_init_validates_tool_use_behavior() -> None:
    with pytest.raises(ValueError):
        Agent(name="x", tool_use_behavior="nope")


def test_agent_compiled_tools_passthrough() -> None:
    @function_tool
    def f(x: int) -> int:
        """noop"""
        return x

    agent = Agent(name="a", tools=[f])
    assert agent.compiled_tools() == [f]


def test_agent_compiled_tools_wraps_callable() -> None:
    def plain(x: int) -> int:
        """add one"""
        return x + 1

    agent = Agent(name="a", tools=[plain])
    tools = agent.compiled_tools()
    assert len(tools) == 1
    assert tools[0].name == "plain"


def test_agent_compiled_tools_includes_agent_as_tool() -> None:
    sub = Agent(name="Sub", instructions="sub", model="gpt-4o-mini")
    parent = Agent(name="Parent", instructions="parent", model="gpt-4o-mini", tools=[sub])
    tools = parent.compiled_tools()
    assert len(tools) == 1
    assert tools[0].name == "sub"


def test_agent_compiled_handoff_tools_default_name() -> None:
    booking = Agent(name="Booking Agent", instructions="b")
    triage = Agent(name="Triage", handoffs=[booking])
    handoff_tools, by_name = triage.compiled_handoff_tools()
    assert len(handoff_tools) == 1
    assert handoff_tools[0].name == "transfer_to_booking_agent"
    assert by_name["transfer_to_booking_agent"] is booking


def test_agent_compiled_handoff_tools_with_handoff_obj() -> None:
    from vtx.sdk import handoff

    target = Agent(name="Target", instructions="t")
    parent = Agent(name="Parent", handoffs=[handoff(target, tool_name_override="ask_target")])
    handoff_tools, by_name = parent.compiled_handoff_tools()
    assert handoff_tools[0].name == "ask_target"
    assert by_name["ask_target"] is target


def test_agent_resolve_instructions_static() -> None:
    a = Agent(name="a", instructions="hello")
    assert a.resolve_instructions() == "hello"


def test_agent_resolve_instructions_callable() -> None:
    a = Agent(name="a", instructions=lambda ctx: f"hello {ctx}")
    assert a.resolve_instructions("world") == "hello world"


def test_agent_build_system_prompt_basic() -> None:
    a = Agent(name="a", instructions="You are helpful.")
    prompt = a.build_system_prompt()
    assert "You are helpful." in prompt


def test_agent_build_system_prompt_with_output_type() -> None:
    class Out(BaseModel):
        x: int

    a = Agent(name="a", instructions="x", output_type=Out)
    prompt = a.build_system_prompt()
    assert "Output format" in prompt
    assert "Out" in prompt


def test_agent_clone_overrides() -> None:
    a = Agent(name="a", instructions="x", model="gpt-4o")
    b = a.clone(name="b", instructions="y")
    assert b.name == "b"
    assert b.instructions == "y"
    assert a.instructions == "x"  # unchanged


def test_agent_as_tool_default_name() -> None:
    sub = Agent(name="My Sub Agent", model="gpt-4o-mini")
    parent = Agent(name="Parent", tools=[sub])
    tools = parent.compiled_tools()
    assert tools[0].name == "my_sub_agent"


@pytest.mark.asyncio
async def test_runner_run_sync_text(text_provider) -> None:
    agent = Agent(name="Bot", provider=text_provider)
    result = Runner.run_sync(agent, "hello")
    assert result.final_output == "Hello, world!"
    assert result.agent_name == "Bot"
    assert result.stop_reason.value == "stop"


@pytest.mark.asyncio
async def test_runner_run_async_text(text_provider) -> None:
    agent = Agent(name="Bot", provider=text_provider)
    result = await Runner.run(agent, "hello")
    assert result.final_output == "Hello, world!"


@pytest.mark.asyncio
async def test_runner_with_in_memory_session(text_provider) -> None:
    session = InMemorySession()
    agent = Agent(name="Bot", provider=text_provider)
    await Runner.run(agent, "first message", session=session)
    await Runner.run(agent, "second message", session=session)
    # Session should have 4 items now: 2 user, 2 assistant
    items = await session.get_items()
    assert len(items) == 4


@pytest.mark.asyncio
async def test_runner_to_input_list_round_trip(text_provider) -> None:
    agent = Agent(name="Bot", provider=text_provider)
    result = await Runner.run(agent, "hi")
    items = result.to_input_list()
    assert len(items) == 1  # assistant message
    # Second call should use the items as input history.
    r2 = await Runner.run(agent, items)
    assert r2.final_output == "Hello, world!"


@pytest.mark.asyncio
async def test_runner_tool_call_scenario(tool_provider) -> None:
    @function_tool
    def noop_tool() -> str:
        return "done"

    agent = Agent(name="Bot", provider=tool_provider, tools=[noop_tool])
    # Provider is the "thinking_text_tool" scenario which calls a "read" tool,
    # but we only have "noop_tool". The call will go to unknown_tool, which errors
    # but the turn still completes. The important thing is that the run completes.
    result = await Runner.run(agent, "hi", max_turns=2)
    # The mock calls a "read" tool, so the result will reflect that error
    # (the mock's tool name doesn't match our registry). The SDK still
    # completes because the runner doesn't fail on unknown tools.
    assert result is not None


@pytest.mark.asyncio
async def test_runner_max_turns_limit(tool_provider) -> None:
    agent = Agent(name="Bot", provider=tool_provider)
    result = await Runner.run(agent, "hi", max_turns=1)
    # With max_turns=1, we should have only one turn's worth of output.
    # The mock keeps requesting tool calls; the runner should stop early.
    assert result is not None
    # First turn produces some output; we don't expect more than one turn's items.
    assert len(result.new_items) <= 5


@pytest.mark.asyncio
async def test_runner_session_input_callback(text_provider) -> None:
    session = InMemorySession()

    def keep_last_only(history: list, new_input: list) -> list:
        return history[-1:] + new_input

    agent = Agent(name="Bot", provider=text_provider)
    await Runner.run(agent, "first", session=session)
    result = await Runner.run(
        agent,
        "second",
        session=session,
        run_config=RunConfig(session_input_callback=keep_last_only),
    )
    assert result.final_output == "Hello, world!"


def test_runner_run_streamed_basic(text_provider) -> None:
    agent = Agent(name="Bot", provider=text_provider)
    streamed = Runner.run_streamed(agent, "hello")

    async def collect() -> None:
        async for _ in streamed:
            pass

    asyncio.run(collect())
    assert streamed._done is True
    assert streamed.result.final_output == "Hello, world!"


@pytest.mark.asyncio
async def test_runner_with_structured_output(text_provider) -> None:

    # Build a custom provider that returns a JSON object as text.
    from vtx.llm.base import BaseProvider, LLMStream, ProviderConfig

    class JsonProvider(BaseProvider):
        name = "json"

        def __init__(self) -> None:
            super().__init__(ProviderConfig(model="json"))

        async def _stream_impl(self, messages, *, system_prompt=None, tools=None, **kw):
            s = LLMStream()
            s._id = "j-1"
            s._usage = None
            from vtx.core.types import StopReason, StreamDone, TextPart

            async def it():
                yield TextPart(text='{"value": 42}')
                yield StreamDone(stop_reason=StopReason.STOP)

            s.set_iterator(it())
            return s

        def should_retry_for_error(self, error):
            return False

    class Out(BaseModel):
        value: int

    agent = Agent(name="Bot", provider=JsonProvider(), output_type=Out)
    result = await Runner.run(agent, "hi")
    assert isinstance(result.final_output, Out)
    assert result.final_output.value == 42
