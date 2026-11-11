"""ACP ``usage_update`` reports context occupancy, not cumulative spend.

The bug this pins: the server sent ``total_usage`` in both fields, so every
client's context meter read 100% (e.g. ``9.2k / 9.2k``) no matter how empty the
window actually was. The extension also hides the meter entirely when either
number is zero, so a zero must not be emitted as a "real" reading.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from vtx.coding_agent.acp import AcpServer, _Session
from vtx.core.events import AgentEndEvent, CompactionEndEvent, TurnEndEvent
from vtx.protocol.types import AssistantMessage, TextContent, Usage

WINDOW = 200_000


class _SessionLog:
    def __init__(self, context_tokens: int) -> None:
        self._context_tokens = context_tokens

    def token_totals(self) -> SimpleNamespace:
        return SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            cache_read_tokens=0,
            cache_write_tokens=0,
            context_tokens=self._context_tokens,
        )


class _Runtime:
    def __init__(self, context_tokens: int) -> None:
        self.model = "test-model"
        self.model_provider = "test-provider"
        self.session = _SessionLog(context_tokens)


def _session(context_tokens: int = 0) -> _Session:
    sess = _Session("/workspace")
    sess.acp_id = "s1"
    sess.runtime = _Runtime(context_tokens)
    return sess


def _recorder(server: AcpServer) -> list[dict]:
    updates: list[dict] = []

    async def _update(session_id: str, update: dict) -> None:
        updates.append(update)

    server._update = _update  # type: ignore[method-assign]
    return updates


def _usage_updates(updates: list[dict]) -> list[tuple[int, int]]:
    return [(u["used"], u["size"]) for u in updates if u.get("sessionUpdate") == "usage_update"]


def _assistant(inp: int, out: int, cached: int = 0) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="hi")],
        usage=Usage(
            input_tokens=inp, output_tokens=out, cache_read_tokens=cached, cache_write_tokens=0
        ),
    )


@pytest.fixture
def known_model(monkeypatch):
    model = SimpleNamespace(context_window=WINDOW, max_tokens=8_000)
    monkeypatch.setattr("vtx.ai.models.get_model", lambda model_id, provider=None: model)


@pytest.mark.asyncio
async def test_turn_end_emits_the_turn_footprint_not_the_run_total(known_model):
    server = AcpServer()
    sess = _session(context_tokens=1_000)  # stale: the turn is not appended yet
    updates = _recorder(server)

    await server._map_event("s1", sess, TurnEndEvent(assistant_message=_assistant(900, 200, 100)))

    assert _usage_updates(updates) == [(1_200, WINDOW)]


@pytest.mark.asyncio
async def test_agent_end_reports_the_session_context_against_the_window(known_model):
    server = AcpServer()
    sess = _session(context_tokens=41_500)
    updates = _recorder(server)

    # A big run total must not be mistaken for context occupancy.
    await server._map_event(
        "s1", sess, AgentEndEvent(total_usage=Usage(input_tokens=90_000, output_tokens=5_000))
    )

    assert _usage_updates(updates) == [(41_500, WINDOW)]


@pytest.mark.asyncio
async def test_compaction_shrink_is_published_immediately(known_model):
    server = AcpServer()
    sess = _session(context_tokens=190_000)
    updates = _recorder(server)

    await server._map_event(
        "s1", sess, CompactionEndEvent(tokens_before=190_000, tokens_after=1_500)
    )

    assert _usage_updates(updates) == [(1_500, WINDOW)]


@pytest.mark.asyncio
async def test_empty_context_is_not_reported():
    """The extension hides the meter on a zero, so a zero must not be sent."""
    server = AcpServer()
    sess = _session(context_tokens=0)
    updates = _recorder(server)

    await server._map_event("s1", sess, TurnEndEvent(assistant_message=None))
    await server._map_event("s1", sess, AgentEndEvent())

    assert _usage_updates(updates) == []


@pytest.mark.asyncio
async def test_unknown_model_falls_back_to_the_configured_window(monkeypatch):
    from vtx.core.config import get_config

    monkeypatch.setattr("vtx.ai.models.get_model", lambda model_id, provider=None: None)
    server = AcpServer()
    sess = _session(context_tokens=500)
    updates = _recorder(server)

    await server._map_event("s1", sess, AgentEndEvent())

    default_window = get_config().agent.default_context_window
    assert _usage_updates(updates) == [(500, default_window)]


@pytest.mark.asyncio
async def test_model_switch_republishes_with_the_new_window(monkeypatch):
    server = AcpServer()
    sess = _session(context_tokens=2_000)
    server._sessions["s1"] = sess
    updates = _recorder(server)

    def switch_model(model) -> None:
        sess.runtime.model = model.id
        sess.runtime.model_provider = model.provider

    sess.runtime.switch_model = switch_model  # type: ignore[attr-defined]
    new_model = SimpleNamespace(
        id="new-model", provider="new-provider", base_url="", context_window=128_000
    )
    monkeypatch.setattr("vtx.ai.models.get_model", lambda model_id, provider=None: new_model)
    # The model list is not what this test is about.
    monkeypatch.setattr(AcpServer, "_config_options", lambda self, sess: [])

    await server.session_set_config_option(
        {"sessionId": "s1", "configId": "model", "value": "new-provider:new-model"}
    )

    assert _usage_updates(updates) == [(2_000, 128_000)]


@pytest.mark.asyncio
async def test_prompt_publishes_context_against_the_window(monkeypatch, tmp_path):
    """End to end through a real agent run: 17 tokens in a 200k window is 0%.

    The mock provider reports 10 input + 5 output + 2 cache-read tokens, so the
    old bug showed up here as ``17 / 17`` -- a full window after one word.
    """
    import os

    from vtx.ai.providers.mock import MockProvider

    # Window resolution has its own tests; what matters here is that the run's
    # context -- not its cumulative spend -- is published.
    monkeypatch.setattr(AcpServer, "_context_window", lambda self, sess: WINDOW)
    monkeypatch.setattr(
        "vtx.agent.runtime.create_provider",
        lambda api_type, config: MockProvider(config, scenario="simple_text"),
    )

    start_cwd = os.getcwd()
    server = AcpServer()
    updates = _recorder(server)
    try:
        session_id = server.session_new({"cwd": str(tmp_path)})["sessionId"]
        result = await server.session_prompt(
            {"sessionId": session_id, "prompt": [{"type": "text", "text": "hi"}]}
        )
    finally:
        os.chdir(start_cwd)

    assert result == {"stopReason": "end_turn"}
    # One distinct reading: turn end and run end agree, so the second is deduped.
    assert _usage_updates(updates) == [(17, WINDOW)]
