from pathlib import Path

from vtx.config import get_config, reset_config


def test_default_local_auth_modes_are_auto(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    reset_config()
    cfg = get_config()

    assert cfg.llm.auth.openai_compat == "auto"
    assert cfg.llm.auth.anthropic_compat == "auto"


def test_local_auth_modes_can_be_overridden(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_dir = home / ".vtx"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yml").write_text(
        """meta:
  config_version: 4

llm:
  default_provider: openai
  default_model: test-model
  default_thinking_level: high

  auth:
    openai_compat: required
    anthropic_compat: none

  system_prompt:
    content: test
    git_context: true

ui:
  theme: gruvbox-dark
  collapse_thinking: true

compaction:
  on_overflow: continue
  threshold_percent: 80

agent:
  max_turns: 500
  default_context_window: 200000



permissions:
  mode: prompt
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", lambda: home)

    reset_config()
    cfg = get_config()

    assert cfg.llm.auth.openai_compat == "required"
    assert cfg.llm.auth.anthropic_compat == "none"
