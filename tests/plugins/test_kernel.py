"""Tests for the plugin kernel: context, effects, and service resolution."""

from __future__ import annotations

import pytest

from vtx.plugins import Context, ServiceMissingError, plugin
from vtx.plugins.context import STANDARD_KEYS


def test_provide_and_read_by_key():
    ctx = Context()
    ctx.provide("tools", {"read": 1})
    assert ctx.tools == {"read": 1}
    assert ctx["tools"] == {"read": 1}
    assert ctx.require("tools") == {"read": 1}
    assert "tools" in ctx
    assert ctx.get("tools") == {"read": 1}
    assert ctx.get("absent", "fallback") == "fallback"


def test_missing_service_raises_with_key_name():
    ctx = Context(label="root")
    with pytest.raises(ServiceMissingError, match="toolbelt"):
        ctx.require("toolbelt")


def test_effects_unwind_in_reverse_order():
    ctx = Context()
    order: list[int] = []
    for index in range(3):
        ctx.effect(lambda index=index: order.append(index))
    ctx.dispose()
    assert order == [2, 1, 0]


def test_effect_exception_does_not_stop_the_rest():
    ctx = Context()
    order: list[str] = []

    def boom() -> None:
        raise RuntimeError("no")

    ctx.effect(boom)
    ctx.effect(lambda: order.append("second"))
    ctx.dispose()
    assert order == ["second"]


def test_scope_reads_parent_services_and_shadows_them():
    root = Context()
    root.provide("tools", "root-tools")
    scope = root.scope("agent-a")
    assert scope.tools == "root-tools"
    scope.provide("tools", "scoped-tools")
    assert scope.tools == "scoped-tools"
    assert root.tools == "root-tools"


def test_scope_disposal_restores_the_parent_service():
    root = Context()
    root.provide("tools", "root-tools")
    scope = root.scope()
    scope.provide("tools", "scoped-tools")
    scope.dispose()
    assert root.tools == "root-tools"


def test_hub_is_shared_with_children():
    root = Context()
    scope = root.scope()
    assert scope.hub is root.hub


def test_standard_keys_are_documented():
    assert "tools" in STANDARD_KEYS
    assert "agentLoop" in STANDARD_KEYS
    assert "sessions" in STANDARD_KEYS


def test_unknown_property_raises_attribute_error():
    ctx = Context()
    with pytest.raises(AttributeError):
        _ = ctx.nope


def test_private_key_is_not_reachable_by_dot_access():
    ctx = Context()
    ctx.provide("_hidden", "value")
    assert ctx.get("_hidden") == "value"
    with pytest.raises(AttributeError):
        _ = ctx._hidden


def test_decorator_plugin_needs_no_inject():
    @plugin(name="noop")
    def apply(ctx, config):
        return "ran"

    ctx = Context()
    assert apply.apply(ctx, None) == "ran"
    assert apply.name == "noop"
    assert apply.inject == ()


def test_context_on_registers_and_disposes():
    ctx = Context()
    seen: list[str] = []

    def handler(payload: str) -> None:
        seen.append(payload)

    ctx.on("hello", handler)
    assert ctx.hub.handlers("hello") == (handler,)
    ctx.dispose()
    assert ctx.hub.handlers("hello") == ()


def test_context_on_returns_handler_for_decorator_use():
    ctx = Context()

    @ctx.on("hello")
    def handler(payload: str) -> str:
        return payload

    assert callable(handler)
    assert ctx.hub.handlers("hello") == (handler,)
