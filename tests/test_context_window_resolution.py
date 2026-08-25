"""Regression tests: the agent engine must always see the active model's
real context window so overflow auto-compaction fires at the right point.

Historical bug: compaction triggered at ~160k tokens (80% of the 200k
harness default) on a 1M-token model because the model lookup failed for
the session's stale provider label and nothing re-applied the window.
"""

from vtx.ai.models import ApiType, Model
from vtx.coding_agent.runtime import ConversationRuntime


def _model(model_id: str, provider: str, context_window: int) -> Model:
    return Model(
        id=model_id,
        provider=provider,
        api=ApiType(ApiType.OPENAI_SDK),
        base_url="https://api.example.com/v1",
        max_tokens=131072,
        supports_images=True,
        supports_thinking=False,
        context_window=context_window,
    )


def _runtime(tmp_path, monkeypatch, model: str, provider: str | None) -> ConversationRuntime:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    return ConversationRuntime(
        cwd=str(tmp_path),
        model=model,
        model_provider=provider,
        api_key="test-key",
        base_url=None,
        thinking_level="high",
        tools=[],
    )


def test_prepare_for_run_applies_model_context_window(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "vtx.coding_agent.runtime.get_model",
        lambda model_id, provider=None: _model(model_id, provider or "kilo", 1_048_576),
    )
    runtime = _runtime(tmp_path, monkeypatch, "stealth/ox-alpha", "kilo")
    runtime.initialize()
    agent = runtime.prepare_for_run()
    assert agent.config.context_window == 1_048_576
    assert agent.config.max_output_tokens == 131072


def test_stale_provider_label_falls_back_to_catalog_wide_lookup(tmp_path, monkeypatch):
    """Session resumed under a legacy provider label ('openai' recorded for a
    custom gateway): the filtered lookup misses but the model is still known."""
    dynamic_calls = []

    def fake_get_model(model_id, provider=None):
        return None

    def fake_find_dynamic(model_id, provider=None):
        dynamic_calls.append((model_id, provider))
        if provider is None:
            return _model(model_id, "kilo", 1_048_576)
        return None

    monkeypatch.setattr("vtx.coding_agent.runtime.get_model", fake_get_model)
    monkeypatch.setattr("vtx.coding_agent.runtime.find_dynamic_model", fake_find_dynamic)

    runtime = _runtime(tmp_path, monkeypatch, "stealth/ox-alpha", "openai")
    runtime.initialize()
    agent = runtime.prepare_for_run()

    assert ("stealth/ox-alpha", None) in dynamic_calls
    assert agent.config.context_window == 1_048_576


def test_unknown_model_falls_back_to_harness_default(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.coding_agent.runtime.get_model", lambda *a, **k: None)
    monkeypatch.setattr("vtx.coding_agent.runtime.find_dynamic_model", lambda *a, **k: None)

    runtime = _runtime(tmp_path, monkeypatch, "totally-unknown", "nowhere")
    runtime.initialize()
    agent = runtime.prepare_for_run()
    # Engine treats None as "use harness default" (200k -> compact at 80%);
    # no crash, explicit None so downstream defaulting is deliberate.
    assert agent.config.context_window is None


def test_switch_model_updates_context_window_immediately(tmp_path, monkeypatch):
    def fake_get_model(model_id, provider=None):
        windows = {"gpt-4o": 128_000, "mega-model": 1_048_576}
        if model_id in windows:
            return _model(model_id, provider or "openai", windows[model_id])
        return None

    monkeypatch.setattr("vtx.coding_agent.runtime.get_model", fake_get_model)
    monkeypatch.setattr(
        "vtx.coding_agent.runtime.get_max_tokens", lambda model_id: None, raising=False
    )

    runtime = _runtime(tmp_path, monkeypatch, "gpt-4o", "openai")
    runtime.initialize()
    agent = runtime.prepare_for_run()
    assert agent.config.context_window == 128_000

    new_model = fake_get_model("mega-model", "openai")
    runtime.switch_model(new_model)

    # The window must update immediately on switch, not just on the next run.
    assert runtime.model == "mega-model"
    assert runtime.agent.config.context_window == 1_048_576


def test_initialize_keeps_requested_provider_label_when_model_unknown(tmp_path, monkeypatch):
    """An unknown model must NOT relabel the provider as the engine class
    name ("openai"); the user's requested label is preserved."""
    monkeypatch.setattr("vtx.coding_agent.runtime.get_model", lambda *a, **k: None)
    monkeypatch.setattr("vtx.coding_agent.runtime.find_dynamic_model", lambda *a, **k: None)

    runtime = _runtime(tmp_path, monkeypatch, "stealth/ox-alpha", "kilo")
    runtime.initialize()

    assert runtime.model_provider == "kilo"


def test_initialize_heals_engine_class_name_provider_label(tmp_path, monkeypatch):
    """Sessions recorded with the engine class name ("openai") instead of the
    real catalog provider are healed at initialize time."""
    real = _model("stealth/ox-alpha", "kilo", 1_048_576)

    def fake_get_model(model_id, provider=None):
        if provider is None or provider == "kilo":
            return real
        return None

    monkeypatch.setattr("vtx.coding_agent.runtime.get_model", fake_get_model)
    monkeypatch.setattr(
        "vtx.coding_agent.runtime.find_dynamic_model",
        lambda model_id, provider=None: real if provider is None else None,
    )

    runtime = _runtime(tmp_path, monkeypatch, "stealth/ox-alpha", "openai")
    runtime.initialize()

    assert runtime.model_provider == "kilo"
