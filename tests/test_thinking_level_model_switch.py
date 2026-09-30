"""Switching to a model must not leave a level that model cannot send.

Reported with `stealth/space-bunny-alpha` on the kilo gateway: the model
advertises low/medium/high/xhigh (plus max) but the info bar sat on "none".
`initialize()` clamps the level against the model's catalog entry, but
`switch_model` did not, so the level carried over from the previous model and
the bar showed a tier the new model has no wire spelling for.

The catalog is patched rather than read from disk so the test does not depend
on a local provider cache being present.
"""

import pytest

from vtx.ai.agent import runtime as runtime_mod
from vtx.ai.agent.runtime import ConversationRuntime
from vtx.ai.models import ApiType, Model

# The shape reported for stealth/space-bunny-alpha: reasoning on, and an effort
# ladder that deliberately excludes "none".
BUNNY_MAP = {
    "off": None,
    "minimal": None,
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
}

PLAIN = Model(
    id="plain-model",
    provider="openai",
    api=ApiType(ApiType.OPENAI_SDK),
    base_url="https://api.openai.com/v1",
    max_tokens=8192,
    supports_images=False,
    supports_thinking=False,
    context_window=128000,
)


def _bunny() -> Model:
    return Model(
        id="bunny",
        provider="kilo",
        api=ApiType(ApiType.OPENAI_SDK),
        base_url="https://api.kilo.ai/api/gateway",
        max_tokens=524288,
        supports_images=True,
        supports_thinking=True,
        context_window=1000000,
        thinking_level_map=BUNNY_MAP,
    )


@pytest.fixture
def catalog(monkeypatch):
    """Resolve only the two models this test uses."""
    entries = {"bunny": _bunny(), "plain-model": PLAIN}
    monkeypatch.setattr(
        runtime_mod, "get_model", lambda model_id, provider=None: entries.get(model_id)
    )
    return entries


def _runtime(tmp_path, model, provider, level):
    return ConversationRuntime(
        cwd=str(tmp_path),
        model=model,
        model_provider=provider,
        api_key="test-key",
        base_url=None,
        thinking_level=level,
        tools=[],
    )


def test_switching_into_a_model_clamps_the_carried_over_level(tmp_path, catalog):
    runtime = _runtime(tmp_path, "plain-model", "openai", "none")
    runtime.initialize()
    assert runtime.thinking_level == "none"

    runtime.switch_model(_bunny())

    levels = runtime.effective_thinking_levels
    assert "low" in levels
    assert runtime.thinking_level in levels, (
        f"level {runtime.thinking_level!r} is not offered by the new model ({levels})"
    )


def test_the_clamped_level_reaches_the_provider_and_session(tmp_path, catalog):
    runtime = _runtime(tmp_path, "plain-model", "openai", "none")
    runtime.initialize()

    runtime.switch_model(_bunny())

    assert runtime.provider.thinking_level == runtime.thinking_level
    assert runtime.session.thinking_level == runtime.thinking_level


def test_a_supported_level_is_left_alone(tmp_path, catalog):
    runtime = _runtime(tmp_path, "plain-model", "openai", "low")
    runtime.initialize()

    runtime.switch_model(_bunny())

    assert runtime.thinking_level == "low"


def test_startup_clamps_the_same_way(tmp_path, catalog):
    # initialize() already did this; the switch path had to match it.
    runtime = _runtime(tmp_path, "bunny", "kilo", "none")
    runtime.initialize()

    assert runtime.thinking_level in runtime.effective_thinking_levels


def test_a_model_with_no_reasoning_is_untouched(tmp_path, catalog):
    # The clamp must not invent an effort level for a model that has none.
    runtime = _runtime(tmp_path, "bunny", "kilo", "none")
    runtime.initialize()
    assert runtime.thinking_level != "none"

    runtime.switch_model(PLAIN)

    assert runtime.effective_thinking_levels == ["none"]
    assert runtime.thinking_level == "none"
