from __future__ import annotations

import pytest

from vtx_claw.config.schema import ClawConfig


@pytest.fixture()
def cfg() -> ClawConfig:
    return ClawConfig()


def test_default_llm_config_values(cfg: ClawConfig):
    assert cfg.llm.default_model == "gpt-4o"
    assert cfg.llm.provider == "openai"
    assert cfg.llm.deepseek["model"] == "deepseek-chat"
    assert cfg.llm.gemini["model"] == "gemini-2.0-flash"
    assert cfg.llm.grok["model"] == "grok-3"
    assert cfg.llm.kimi["model"] == "moonshot-v1-128k"
    assert cfg.llm.glm["model"] == "glm-4-flash"


def test_llm_api_keys_roundtrip(tmp_path):
    from vtx_claw.config.schema import load_claw_config, save_claw_config

    cfg = ClawConfig()
    cfg.llm.deepseek["api_key"] = "sk-deepseek-test"
    cfg.llm.gemini["api_key"] = "sk-gemini-test"
    cfg.llm.grok["api_key"] = "sk-grok-test"
    p = tmp_path / "claw.yml"
    save_claw_config(cfg, p)
    loaded = load_claw_config(p)
    assert loaded.llm.deepseek["api_key"] == "sk-deepseek-test"
    assert loaded.llm.gemini["api_key"] == "sk-gemini-test"
    assert loaded.llm.grok["api_key"] == "sk-grok-test"


def test_provider_selection_uses_config(cfg: ClawConfig):
    assert cfg.llm.provider == "openai"
    cfg.llm.provider = "deepseek"
    assert cfg.llm.provider == "deepseek"
    cfg.llm.provider = "gemini"
    assert cfg.llm.provider == "gemini"


def test_custom_provider_config():
    cfg = ClawConfig()
    cfg.llm.custom["base_url"] = "https://my-llm.example.com/v1"
    cfg.llm.custom["api_key"] = "sk-custom"
    cfg.llm.custom["model"] = "my-model"
    assert cfg.llm.custom["base_url"] == "https://my-llm.example.com/v1"
    assert cfg.llm.custom["model"] == "my-model"


def test_ensure_runtime_resolves_from_vtx(monkeypatch):
    import vtx_claw.agent
    from vtx.config import LastSelectedConfig
    from vtx_claw.config.schema import ClawConfig

    # Mock get_last_selected to returns gemini provider and gemini-2.5-pro model
    mock_last = LastSelectedConfig(
        model_id="gemini-2.5-pro", provider="gemini", thinking_level="high", agent=None
    )
    monkeypatch.setattr("vtx_claw.agent.get_last_selected", lambda: mock_last)

    # Mock get_dynamic_api_key to return a provider specific key
    monkeypatch.setattr(
        "vtx.llm.oauth.dynamic.get_dynamic_api_key", lambda p: f"sk-{p}-vtx-mock-key"
    )

    cfg = ClawConfig()
    cfg.llm.provider = "openai"  # Ignored because last_selected has priority
    cfg.llm.default_model = "gpt-4o"  # Ignored because last_selected has priority

    handler = vtx_claw.agent.AgentHandler(cfg)

    class MockRuntime:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.model = kwargs.get("model")
            self.model_provider = kwargs.get("model_provider")
            self.api_key = kwargs.get("api_key")
            self.session = None
            self.agent = None

        def set_loaded_extensions(self, ext):
            pass

        def initialize(self):
            class InitResult:
                provider_error = None

            return InitResult()

    monkeypatch.setattr("vtx_claw.agent.ClawConversationRuntime", MockRuntime)
    monkeypatch.setattr("vtx_claw.agent.get_model", lambda m, p: None)

    import asyncio

    runtime = asyncio.run(handler.ensure_runtime())

    assert runtime.model == "gemini-2.5-pro"
    assert runtime.model_provider == "gemini"
    assert runtime.api_key == "sk-gemini-vtx-mock-key"
