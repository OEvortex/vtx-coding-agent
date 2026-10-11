"""Tests for the projection seam: incremental folds over the session log."""

from __future__ import annotations

import pytest

from vtx.plugins import ServiceMissingError, boot
from vtx.plugins.services import Projection, ProjectionService


class _Event:
    def __init__(self, type_: str, **data: object) -> None:
        self.type = type_
        self._data = data

    def __getattr__(self, name: str) -> object:
        try:
            return self.__dict__["_data"][name]
        except KeyError:
            raise AttributeError(name) from None


class _Message:
    def __init__(self, role: str, usage: object | None = None) -> None:
        self.role = role
        self.usage = usage


class _Usage:
    def __init__(self, **fields: int) -> None:
        for key, value in fields.items():
            setattr(self, key, value)


def test_only_the_events_since_the_last_call_are_applied():
    service = ProjectionService()
    calls: list[int] = []

    def apply(state, event):
        calls.append(1)
        return state + 1

    service.register(Projection(key="n", init=lambda: 0, apply=apply))
    events = [_Event("a"), _Event("b")]
    assert service.state_of("n", events) == 2
    assert len(calls) == 2
    # A growing list re-applies nothing.
    events.append(_Event("c"))
    assert service.state_of("n", events) == 3
    assert len(calls) == 3


def test_an_irrelevant_event_returns_the_same_state_object():
    service = ProjectionService()
    state = object()
    service.register(Projection(key="k", init=lambda: state, apply=lambda s, e: s))
    assert service.state_of("k", [_Event("x"), _Event("y")]) is state


def test_an_unknown_key_raises():
    service = ProjectionService()
    with pytest.raises(ServiceMissingError, match="nothing-registered"):
        service.state_of("nothing-registered")


def test_snapshot_reads_every_key_in_one_call():
    service = ProjectionService()
    service.register(Projection(key="a", init=lambda: 1, apply=lambda s, e: s))
    service.register(Projection(key="b", init=lambda: 2, apply=lambda s, e: s))
    assert service.snapshot() == {"a": 1, "b": 2}
    assert service.snapshot(["a"]) == {"a": 1}
    assert service.keys() == ["a", "b"]


def test_reregistering_replaces_the_definition_and_the_folded_state():
    service = ProjectionService()
    service.register(Projection(key="k", init=lambda: 1, apply=lambda s, e: s + 1))
    service.state_of("k", [_Event("x")])
    service.register(Projection(key="k", init=lambda: 100, apply=lambda s, e: s))
    assert service.state_of("k", [_Event("x")]) == 100


def test_reset_keeps_definitions_and_drops_state():
    service = ProjectionService()
    service.register(Projection(key="k", init=lambda: 0, apply=lambda s, e: s + 1))
    service.state_of("k", [_Event("x")])
    service.reset()
    assert service.state_of("k", [_Event("x")]) == 1
    assert service.keys() == ["k"]


def test_a_projection_key_must_be_non_empty():
    service = ProjectionService()
    with pytest.raises(ValueError, match="non-empty"):
        service.register(Projection(key="", init=lambda: 1, apply=lambda s, e: s))


def test_the_built_in_uses_projection_folds_token_usage():
    """The registered projection, driven by real session-like entries."""
    service = ProjectionService()
    from vtx.plugins.builtins.services.projections import _make_uses_projection

    unit = _make_uses_projection()
    service.register(unit)

    entries = [
        _Event("message", message=_Message("assistant", _Usage(input_tokens=10, output_tokens=5))),
        _Event("message", message=_Message("user")),  # no usage: unchanged
        _Event("thinking", message=_Message("assistant")),  # wrong type: unchanged
        _Event(
            "message", message=_Message("assistant", _Usage(input_tokens=7, cache_read_tokens=3))
        ),
    ]
    state = service.state_of("uses", entries)
    assert state == {"assistant": {"input_tokens": 17, "output_tokens": 5, "cache_read_tokens": 3}}


def test_the_uses_projection_ignores_a_zero_usage_field():
    from vtx.plugins.builtins.services.projections import _make_uses_projection

    unit = _make_uses_projection()
    state = unit.init()
    state = unit.apply(
        state, _Event("message", message=_Message("assistant", _Usage(output_tokens=0)))
    )
    assert state == {"assistant": {}}


def test_the_composition_registers_the_projection_service():
    host = boot()
    try:
        ctx = host.base
        assert "sessionProjections" in ctx
        assert ctx.sessionProjections.keys() == ["uses"]
    finally:
        host.dispose()


def test_projections_survive_a_restart_of_the_composition():
    """The definitions live in the row, so a fresh boot re-registers them."""
    first = boot()
    first.dispose()
    second = boot()
    try:
        assert second.base.sessionProjections.keys() == ["uses"]
    finally:
        second.dispose()
