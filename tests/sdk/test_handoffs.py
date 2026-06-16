"""Tests for handoffs."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from vtx.sdk import Agent, handoff
from vtx.sdk.handoffs import Handoff, HandoffInputData


def test_handoff_default_name() -> None:
    target = Agent(name="Booking Agent")
    h = handoff(target)
    assert h.name == "transfer_to_booking_agent"
    assert h.target_agent is target


def test_handoff_override_name() -> None:
    target = Agent(name="Refund")
    h = handoff(
        target, tool_name_override="ask_refund", tool_description_override="Ask refund agent"
    )
    assert h.name == "ask_refund"
    assert h.description == "Ask refund agent"


def test_handoff_direct_construction() -> None:
    target = Agent(name="Target")
    h = Handoff(agent=target)
    assert h.name == "transfer_to_target"
    assert h.target_agent is target


def test_handoff_format_call() -> None:
    target = Agent(name="Booking")
    h = handoff(target)
    text = h.format_call(h.params())
    assert "Booking" in text


@pytest.mark.asyncio
async def test_handoff_execute_returns_marker() -> None:
    target = Agent(name="Target")
    h = handoff(target)
    result = await h.execute(h.params())
    assert result.success is True
    assert result.result is not None and "Target" in result.result


@pytest.mark.asyncio
async def test_handoff_with_on_handoff_callback() -> None:
    called = []

    def cb(ctx):
        called.append("yes")

    target = Agent(name="Target")
    h = handoff(target, on_handoff=cb)
    await h.execute(h.params())
    assert called == ["yes"]


@pytest.mark.asyncio
async def test_handoff_with_async_on_handoff() -> None:
    called = []

    async def cb(ctx):
        await asyncio.sleep(0)
        called.append("async")

    target = Agent(name="Target")
    h = handoff(target, on_handoff=cb)
    await h.execute(h.params())
    assert called == ["async"]


def test_handoff_with_input_type() -> None:
    class HandoffData(BaseModel):
        reason: str

    target = Agent(name="Target")
    h = handoff(target, input_type=HandoffData)
    schema = h.params.model_json_schema()
    assert "reason" in schema["properties"]


def test_handoff_input_filter_callable() -> None:
    target = Agent(name="Target")

    def filt(data: HandoffInputData) -> HandoffInputData:
        return data

    h = handoff(target, input_filter=filt)
    assert h.input_filter is filt


def test_agent_handoffs_from_agents() -> None:
    target = Agent(name="Target")
    parent = Agent(name="Parent", handoffs=[target])
    tools, by_name = parent.compiled_handoff_tools()
    assert len(tools) == 1
    assert tools[0].name == "transfer_to_target"
    assert by_name["transfer_to_target"] is target


def test_agent_handoffs_from_handoff_objects() -> None:
    target = Agent(name="Target")
    h = handoff(target, tool_name_override="ask_target")
    parent = Agent(name="Parent", handoffs=[h])
    tools, _by_name = parent.compiled_handoff_tools()
    assert tools[0].name == "ask_target"
