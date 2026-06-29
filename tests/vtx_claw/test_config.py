from __future__ import annotations

from pathlib import Path

import yaml

from vtx_claw.config.schema import (
    CHANNEL_FIELD_NAMES,
    ClawConfig,
    IsolationConfig,
    LLMConfig,
    MemoryConfig,
    PersonaConfig,
    SecurityConfig,
    SkillsConfig,
    ToolsConfig,
    VoiceConfig,
    load_claw_config,
    save_claw_config,
)


def test_default_config_has_new_fields():
    cfg = ClawConfig()
    assert isinstance(cfg.llm, LLMConfig)
    assert isinstance(cfg.memory, MemoryConfig)
    assert isinstance(cfg.skills, SkillsConfig)
    assert isinstance(cfg.isolation, IsolationConfig)
    assert isinstance(cfg.persona, PersonaConfig)
    assert isinstance(cfg.voice, VoiceConfig)
    assert isinstance(cfg.security, SecurityConfig)
    assert isinstance(cfg.tools, ToolsConfig)


def test_default_llm_values():
    cfg = ClawConfig()
    assert cfg.llm.default_model == "gpt-4o"
    assert cfg.llm.provider == "openai"
    assert cfg.llm.deepseek["model"] == "deepseek-chat"
    assert cfg.llm.gemini["model"] == "gemini-2.0-flash"
    assert cfg.llm.grok["model"] == "grok-3"


def test_default_security_values():
    cfg = ClawConfig()
    assert cfg.security.default_policy == "pairing"
    assert cfg.security.default_preset == "standard"
    assert "python" in cfg.security.safe_bins


def test_channels_includes_slack_and_signal():
    cfg = ClawConfig()
    assert "slack" in CHANNEL_FIELD_NAMES
    assert "signal" in CHANNEL_FIELD_NAMES
    assert not cfg.channels.slack.enabled
    assert not cfg.channels.signal.enabled


def test_config_roundtrip(tmp_path: Path):
    p = tmp_path / "claw.yml"
    cfg = ClawConfig()
    save_claw_config(cfg, p)
    assert p.exists()
    loaded = load_claw_config(p)
    assert loaded.gateway.port == 18789
    assert loaded.security.default_preset == "standard"
    assert loaded.llm.gemini["model"] == "gemini-2.0-flash"


def test_config_loaded_from_yaml(tmp_path: Path):
    p = tmp_path / "claw.yml"
    p.write_text(
        yaml.dump(
            {
                "gateway": {"port": 9999},
                "llm": {"provider": "deepseek", "deepseek": {"api_key": "sk-test"}},
                "channels": {"slack": {"enabled": True, "bot_token": "xoxb-test"}},
            }
        )
    )
    cfg = load_claw_config(p)
    assert cfg.gateway.port == 9999
    assert cfg.llm.provider == "deepseek"
    assert cfg.llm.deepseek["api_key"] == "sk-test"
    assert cfg.channels.slack.enabled is True
    assert cfg.channels.slack.bot_token == "xoxb-test"
