"""Tests for provider-level hooks (pi before_provider_headers/request parity).

Covers the transport registry (:mod:`vtx.ai.provider_hooks`) and its bridge
onto the extension EventBus.
"""

from __future__ import annotations

import pytest

import vtx.ai.agent.extensions as ext_mod
from vtx.ai.agent.extensions import (
    BEFORE_PROVIDER_HEADERS,
    BEFORE_PROVIDER_REQUEST,
    EventBus,
    install_provider_bridge,
)
from vtx.ai.provider_hooks import (
    prepare_request,
    register_headers_listener,
    register_request_listener,
    reset_listeners,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_listeners()
    ext_mod._PROVIDER_BRIDGE_BUS = None
    yield
    reset_listeners()
    ext_mod._PROVIDER_BRIDGE_BUS = None


# =============================================================================
# Registry semantics
# =============================================================================


@pytest.mark.asyncio
async def test_no_listeners_payload_untouched():
    payload = {"model": "gpt-4o", "messages": []}
    extra_headers, result = await prepare_request(provider="openai", payload=payload)
    assert extra_headers == {}
    assert result is payload


@pytest.mark.asyncio
async def test_sync_header_listener_adds_and_deletes():
    def add(headers, context):
        headers["x-session-id"] = "abc"
        headers["x-drop-me"] = None

    register_headers_listener(add)
    extra_headers, _ = await prepare_request(provider="openai", payload={"model": "m"})
    assert extra_headers == {"x-session-id": "abc"}


@pytest.mark.asyncio
async def test_async_header_listener_supported():
    async def add(headers, context):
        headers["x-trace"] = context["provider"]

    register_headers_listener(add)
    extra_headers, _ = await prepare_request(provider="anthropic", payload={"model": "claude"})
    assert extra_headers == {"x-trace": "anthropic"}


@pytest.mark.asyncio
async def test_header_listener_exception_does_not_fail_request():
    def boom(headers, context):
        raise RuntimeError("boom")

    register_headers_listener(boom)

    def good(headers, context):
        headers["x-ok"] = "1"

    register_headers_listener(good)
    extra_headers, _ = await prepare_request(provider="openai", payload={"model": "m"})
    assert extra_headers == {"x-ok": "1"}


@pytest.mark.asyncio
async def test_request_listener_replaces_payload():
    def rewrite(payload, context):
        return {"payload": {**payload, "temperature": 0}}

    register_request_listener(rewrite)
    _, result = await prepare_request(provider="openai", payload={"model": "m"})
    assert result["temperature"] == 0


@pytest.mark.asyncio
async def test_request_listeners_chain_off_replacements():
    seen = []

    def first(payload, context):
        return {"payload": {**payload, "v": 1}}

    def second(payload, context):
        seen.append(payload.get("v"))
        return {"payload": {**payload, "v": 2}}

    register_request_listener(first)
    register_request_listener(second)
    _, result = await prepare_request(provider="openai", payload={"model": "m"})
    # Second listener saw the first listener's replacement...
    assert seen == [1]
    # ...and its own replacement is what ships.
    assert result["v"] == 2


@pytest.mark.asyncio
async def test_in_place_mutation_works_without_return():
    def mutate(payload, context):
        payload["temperature"] = 0.5

    register_request_listener(mutate)
    original = {"model": "m"}
    _, result = await prepare_request(provider="openai", payload=original)
    assert result["temperature"] == 0.5


# =============================================================================
# Bridge onto the extension bus
# =============================================================================


@pytest.mark.asyncio
async def test_bridge_fires_bus_events_with_ctx():
    bus = EventBus()
    install_provider_bridge(bus)
    seen = {}

    def on_headers(event, payload):
        seen["headers_event"] = (payload["provider"], payload["model"])
        payload["headers"]["x-ext"] = "yes"

    async def on_request(event, payload, ctx):
        seen["has_ctx"] = hasattr(ctx, "ui")
        return {"payload": {**payload["payload"], "temperature": 0}}

    bus.on(BEFORE_PROVIDER_HEADERS, on_headers)
    bus.on(BEFORE_PROVIDER_REQUEST, on_request)

    extra_headers, result = await prepare_request(provider="openai", payload={"model": "gpt-4o"})
    assert seen["headers_event"] == ("openai", "gpt-4o")
    assert extra_headers == {"x-ext": "yes"}
    assert seen["has_ctx"] is True
    assert result["temperature"] == 0


@pytest.mark.asyncio
async def test_bridge_is_idempotent():
    bus1 = EventBus()
    install_provider_bridge(bus1)
    bus2 = EventBus()
    install_provider_bridge(bus2)  # second call must be a no-op

    fired = []
    bus2.on(BEFORE_PROVIDER_HEADERS, lambda event, payload: fired.append(event))

    await prepare_request(provider="openai", payload={"model": "m"})
    assert fired == []  # listeners still bound to bus1, not bus2


@pytest.mark.asyncio
async def test_extension_api_helper_registers_handler():
    from vtx.ai.agent.extensions import Extension, ExtensionAPI

    bus = EventBus()
    api = ExtensionAPI(
        Extension(name="t", path="/tmp/t.py"), bus, cwd="/tmp", session_file=None, config_dir=None
    )

    @api.on_before_provider_request
    def handler(event, payload):
        return {"payload": {**payload["payload"], "top_p": 0.9}}

    # Bridge not installed here: drive the bus directly to verify registration.
    merged = await bus.emit(BEFORE_PROVIDER_REQUEST, provider="p", model="m", payload={})
    assert merged["payload"]["top_p"] == 0.9
