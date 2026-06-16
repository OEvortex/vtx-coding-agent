"""End-to-end integration tests for the SDK."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from vtx.sdk import Agent, GuardrailFunctionOutput, JSONLSession, Runner, input_guardrail, tool


@pytest.mark.asyncio
async def test_quickstart_pattern(text_provider) -> None:
    """The exact code from the README quickstart section."""

    @tool
    def get_weather(city: str) -> str:
        """Return the weather for a city."""
        return f"Sunny in {city}"

    agent = Agent(
        name="Weather bot", instructions="Be concise.", provider=text_provider, tools=[get_weather]
    )

    result = await Runner.run(agent, "What's the weather in Tokyo?")
    assert result.final_output == "Hello, world!"
    assert result.agent_name == "Weather bot"


@pytest.mark.asyncio
async def test_handoff_pattern(text_provider) -> None:
    """Manager-style: handoffs on the parent agent."""
    booking = Agent(name="Booking", instructions="Book flights.", provider=text_provider)
    triage = Agent(
        name="Triage",
        instructions="Route to specialists.",
        provider=text_provider,
        handoffs=[booking],
    )
    tools, by_name = triage.compiled_handoff_tools()
    assert len(tools) == 1
    assert "transfer_to_booking" in tools[0].name
    assert by_name[tools[0].name] is booking


@pytest.mark.asyncio
async def test_agents_as_tools_pattern(text_provider) -> None:
    sub = Agent(name="Specialist", instructions="x", provider=text_provider)
    parent = Agent(
        name="Manager",
        instructions="Use your tools.",
        provider=text_provider,
        tools=[sub.as_tool()],
    )
    tools = parent.compiled_tools()
    assert len(tools) == 1
    assert tools[0].name == "specialist"


@pytest.mark.asyncio
async def test_session_persistence_pattern(tmp_path) -> None:
    """JSONLSession round-trip via the Runner."""
    path = tmp_path / "sdk-session.jsonl"
    session1 = JSONLSession(path)
    agent = Agent(name="Bot", provider=None)  # set below

    from vtx.llm.providers.mock import MockProvider

    session1_provider = MockProvider(scenario="simple_text")
    agent.provider = session1_provider
    await Runner.run(agent, "first", session=session1)

    # Reload via a fresh session and run again.
    session2 = JSONLSession(path)
    agent2 = Agent(name="Bot", provider=MockProvider(scenario="simple_text"))
    result = await Runner.run(agent2, "second", session=session2)
    assert result.final_output == "Hello, world!"


@pytest.mark.asyncio
async def test_structured_output_pattern() -> None:
    """Agent with output_type=BaseModel."""

    class Event(BaseModel):
        name: str
        date: str

    from vtx.core.types import StopReason, StreamDone, TextPart
    from vtx.llm.base import BaseProvider, LLMStream, ProviderConfig

    class JsonProvider(BaseProvider):
        name = "json"

        def __init__(self) -> None:
            super().__init__(ProviderConfig(model="json"))

        async def _stream_impl(self, messages, *, system_prompt=None, tools=None, **kw):
            s = LLMStream()
            s._id = "j-1"
            s._usage = None

            async def it():
                yield TextPart(text='{"name": "Lunch", "date": "Friday"}')
                yield StreamDone(stop_reason=StopReason.STOP)

            s.set_iterator(it())
            return s

        def should_retry_for_error(self, error):
            return False

    agent = Agent(name="Extractor", provider=JsonProvider(), output_type=Event)
    result = await Runner.run(agent, "Extract from: Lunch on Friday")
    assert isinstance(result.final_output, Event)
    assert result.final_output.name == "Lunch"
    assert result.final_output.date == "Friday"


@pytest.mark.asyncio
async def test_input_guardrail_in_run(text_provider) -> None:
    @input_guardrail
    def block(data):
        return GuardrailFunctionOutput(tripwire_triggered="bad" in (data.input or ""))

    from vtx.sdk import InputGuardrailTripwireTriggered

    agent = Agent(name="Bot", provider=text_provider, input_guardrails=[block])
    with pytest.raises(InputGuardrailTripwireTriggered):
        await Runner.run(agent, "this is bad")
    result = await Runner.run(agent, "this is fine")
    assert result.final_output == "Hello, world!"


@pytest.mark.asyncio
async def test_run_sync_via_thread(text_provider) -> None:
    """``Runner.run_sync`` works from a synchronous context (or a running loop)."""
    from vtx.sdk import Runner

    agent = Agent(name="Bot", provider=text_provider)
    result = Runner.run_sync(agent, "hi")
    assert result.final_output == "Hello, world!"
