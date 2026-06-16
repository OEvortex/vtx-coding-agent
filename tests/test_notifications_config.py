from pathlib import Path

from vtx.config import get_config, reset_config


def test_notifications_disabled_by_default(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    reset_config()
    cfg = get_config()

    assert cfg.notifications.enabled is False
    assert cfg.notifications.volume == 0.5


def test_notifications_can_be_enabled(tmp_path, monkeypatch):
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

notifications:
  enabled: true
  volume: 0.25
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", lambda: home)

    reset_config()
    cfg = get_config()

    assert cfg.notifications.enabled is True
    assert cfg.notifications.volume == 0.25
