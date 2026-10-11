"""Tests for the typed event dispatch modes."""

from __future__ import annotations

import pytest

from vtx.plugins import DispatchMode, EventHub, EventModeError

pytestmark = pytest.mark.asyncio


async def test_emit_is_fire_and_forget_with_no_return():
    hub = EventHub()
    seen: list[str] = []
    hub.on("ping", lambda: seen.append("a"))
    hub.on("ping", lambda: seen.append("b"))
    assert await hub.emit("ping") is None
    assert seen == ["a", "b"]


async def test_serial_collects_results_in_order():
    hub = EventHub()
    hub.declare("step", DispatchMode.SERIAL)
    hub.on("step", lambda x: x + 1)
    hub.on("step", lambda x: x * 10)
    assert await hub.emit("step", 1) == [2, 10]


async def test_parallel_awaits_every_listener():
    hub = EventHub()
    hub.declare("fan", DispatchMode.PARALLEL)
    hub.on("fan", lambda x: x + "b")
    hub.on("fan", lambda x: x + "c")
    assert await hub.emit("fan", "a") == ["ab", "ac"]


async def test_bail_stops_at_the_first_non_none():
    hub = EventHub()
    hub.declare("gate", DispatchMode.BAIL)
    hub.on("gate", lambda: None)
    hub.on("gate", lambda: "denied")
    hub.on("gate", lambda: "never")
    assert await hub.emit("gate") == "denied"


async def test_bail_returns_none_when_nobody_bails():
    hub = EventHub()
    hub.declare("gate", DispatchMode.BAIL)
    hub.on("gate", lambda: None)
    assert await hub.emit("gate") is None


async def test_waterfall_delegates_through_next():
    hub = EventHub()
    hub.declare("wrap", DispatchMode.WATERFALL)

    async def first(value, next):
        return await next(value + "-first")

    async def second(value, next):
        return await next(value + "-second")

    async def terminal(value, next):
        return value + "-terminal"

    hub.on("wrap", first)
    hub.on("wrap", second)
    hub.on("wrap", terminal)
    assert await hub.emit("wrap", "v") == "v-first-second-terminal"


async def test_waterfall_returning_without_next_short_circuits():
    hub = EventHub()
    hub.declare("wrap", DispatchMode.WATERFALL)
    reached: list[str] = []

    async def policy(value, next):
        return "policy-owns-this"

    async def observer(value, next):
        reached.append("downstream")
        return await next(value)

    hub.on("wrap", policy)
    hub.on("wrap", observer)
    assert await hub.emit("wrap", "v") == "policy-owns-this"
    assert reached == []


async def test_waterfall_empty_chain_returns_the_original_value():
    hub = EventHub()
    hub.declare("wrap", DispatchMode.WATERFALL)
    assert await hub.emit("wrap", "original") == "original"


async def test_waterfall_extra_kwargs_reach_every_listener():
    hub = EventHub()
    hub.declare("wrap", DispatchMode.WATERFALL)

    async def record(value, next, **kwargs):
        record.seen = kwargs
        return await next(value)

    async def terminal(value, next):
        return value

    hub.on("wrap", record)
    hub.on("wrap", terminal)
    await hub.emit("wrap", "v", tag="x")
    assert record.seen == {"tag": "x"}


async def test_async_and_sync_handlers_mix():
    hub = EventHub()
    hub.declare("mixed", DispatchMode.SERIAL)

    async def async_handler(value):
        return value + 1

    hub.on("mixed", async_handler)
    hub.on("mixed", lambda value: value + 2)
    assert await hub.emit("mixed", 0) == [1, 2]


async def test_redeclaring_a_mode_conflict_fails_loud():
    hub = EventHub()
    hub.declare("thing", DispatchMode.SERIAL)
    with pytest.raises(EventModeError, match="thing"):
        hub.declare("thing", DispatchMode.BAIL)


async def test_redeclaring_the_same_mode_is_allowed():
    hub = EventHub()
    hub.declare("thing", DispatchMode.SERIAL)
    hub.declare("thing", DispatchMode.SERIAL)
    assert hub.mode("thing") is DispatchMode.SERIAL


async def test_undeclared_events_default_to_emit():
    hub = EventHub()
    seen: list[str] = []
    hub.on("mystery", lambda: seen.append("x"))
    assert await hub.emit("mystery") is None
    assert seen == ["x"]


async def test_off_removes_one_handler_and_keeps_the_rest():
    hub = EventHub()
    keep = lambda: None  # noqa: E731
    drop = lambda: None  # noqa: E731
    hub.on("e", keep)
    hub.on("e", drop)
    hub.off("e", drop)
    assert hub.handlers("e") == (keep,)


async def test_off_is_silent_for_an_unknown_handler():
    hub = EventHub()
    hub.on("e", lambda: None)
    hub.off("e", lambda: None)


async def test_prepend_runs_before_ordinary_registrations():
    hub = EventHub()
    order: list[str] = []
    hub.on("e", lambda: order.append("later"))
    hub.on("e", lambda: order.append("first"), prepend=True)
    await hub.emit("e")
    assert order == ["first", "later"]
