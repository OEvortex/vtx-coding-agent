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
