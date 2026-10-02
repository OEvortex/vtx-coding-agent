from pathlib import Path

import yaml

from vtx.coding_agent.config import (
    CURRENT_CONFIG_VERSION,
    consume_config_warnings,
    get_config,
    reset_config,
)


def test_old_config_is_migrated_and_backed_up(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_dir = home / ".vtx"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.yml"
    config_file.write_text(
        """meta:
  config_version: 2

ui:
  colors:
    warning: "#123456"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", lambda: home)

    reset_config()
    cfg = get_config()

    assert cfg.ui.theme == "gruvbox-dark"
    assert cfg.ui.colors.notice == "#fe8019"

    updated = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert updated["meta"]["config_version"] == CURRENT_CONFIG_VERSION
    assert updated["ui"]["theme"] == "gruvbox-dark"
    assert "colors" not in updated["ui"]
    assert updated["llm"]["auth"]["openai_compat"] == "auto"
    assert updated["llm"]["auth"]["anthropic_compat"] == "auto"
    assert updated["notifications"]["volume"] == 0.5

    backup_files = list(config_dir.glob("config.yml.bak.*"))
    # Only create backup if the file already exists (i.e., has content)
    if config_file.exists():
        assert len(backup_files) == 1
    else:
        assert len(backup_files) == 0

    warnings = consume_config_warnings()
    assert any("Migrated config" in warning for warning in warnings)


def test_v4_config_migrates_notification_volume_without_overwriting_existing_value(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    config_dir = home / ".vtx"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.yml"
    config_file.write_text(
        """meta:
  config_version: 4

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

    updated = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert updated["meta"]["config_version"] == CURRENT_CONFIG_VERSION
    assert updated["notifications"]["volume"] == 0.25

    warnings = consume_config_warnings()
    assert any("Migrated config" in warning for warning in warnings)


def test_v4_config_migrates_missing_notification_volume(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_dir = home / ".vtx"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.yml"
    config_file.write_text(
        """meta:
  config_version: 2

ui:
  colors:
    warning: "#123456"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", lambda: home)

    reset_config()
    cfg = get_config()

    assert cfg.notifications.enabled is False
    assert cfg.notifications.volume == 0.5

    updated = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert updated["meta"]["config_version"] == CURRENT_CONFIG_VERSION
    assert updated["notifications"]["volume"] == 0.5

    warnings = consume_config_warnings()
    assert any("Migrated config" in warning for warning in warnings)


def test_current_version_config_is_not_rewritten(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_dir = home / ".vtx"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.yml"
    original_text = (
        "meta:\n"
        f"  config_version: {CURRENT_CONFIG_VERSION}\n\n"
        "llm:\n"
        "  default_model: custom-model\n"
        "  system_prompt:\n"
        '    content: "custom prompt"\n'
    )
    config_file.write_text(original_text, encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: home)

    reset_config()
    cfg = get_config()

    assert cfg.llm.default_model == "custom-model"
    assert config_file.read_text(encoding="utf-8") == original_text
    # Only check for backup files if the file already exists
    if config_file.exists():
        assert list(config_dir.glob("config.yml.bak.*")) == []

    warnings = consume_config_warnings()
    assert all("Migrated config" not in warning for warning in warnings)


def test_v5_config_replaces_system_prompt_with_current_default(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_dir = home / ".vtx"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.yml"
    config_file.write_text(
        """meta:
  config_version: 5

llm:
  system_prompt:
    git_context: false
    content: |-
      Custom prompt

      # Tool usage

      - Old tool instruction
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", lambda: home)

    reset_config()
    cfg = get_config()

    assert cfg.llm.system_prompt.git_context is True

    updated = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert updated["meta"]["config_version"] == CURRENT_CONFIG_VERSION
    assert "content" not in updated["llm"]["system_prompt"]
    assert updated["llm"]["system_prompt"]["git_context"] is True
    # Only check for backup files if the file already exists
    if config_file.exists():
        assert list(config_dir.glob("config.yml.bak.*"))

    warnings = consume_config_warnings()
    assert any("Migrated config" in warning for warning in warnings)


def test_v1_llm_system_prompt_keys_migrate_to_nested_section(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_dir = home / ".vtx"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.yml"
    config_file.write_text(
        """meta:
  config_version: 1

llm:
  default_model: legacy-model
  system_prompt_git_context: true
  system_prompt: legacy prompt
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", lambda: home)

    reset_config()
    cfg = get_config()

    assert cfg.llm.default_model == "legacy-model"
    assert cfg.llm.system_prompt.git_context is True

    updated = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert updated["meta"]["config_version"] == CURRENT_CONFIG_VERSION
    assert "content" not in updated["llm"]["system_prompt"]
    assert updated["llm"]["system_prompt"]["git_context"] is True

    warnings = consume_config_warnings()
    assert any("Migrated config" in warning for warning in warnings)


def test_v13_mode_key_is_dropped(tmp_path, monkeypatch):
    """v14 retired the REPL-first mode and removes the key.

    Dropped rather than normalised, so a stale ``mode:`` line cannot sit in a
    config suggesting a choice that no longer exists.
    """
    home = tmp_path / "home"
    config_dir = home / ".vtx"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.yml"
    config_file.write_text("meta:\n  config_version: 13\n\nmode: rlm\n", encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: home)
    reset_config()

    get_config()

    # The rewrite is what the user sees on disk, so assert on it rather than on
    # a config attribute: Config does not expose its own version.
    written = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert written["meta"]["config_version"] == CURRENT_CONFIG_VERSION
    assert "mode" not in written

    reset_config()


def test_v15_refine_block_is_dropped(tmp_path, monkeypatch):
    """v16 removes the refine block; an old config still loads without it."""
    home = tmp_path / "home"
    config_dir = home / ".vtx"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.yml"
    config_file.write_text(
        "meta:\n  config_version: 15\n\nrefine:\n  enabled: true\n  turn_interval: 25\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", lambda: home)
    reset_config()

    get_config()

    written = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert written["meta"]["config_version"] == CURRENT_CONFIG_VERSION
    assert "refine" not in written

    reset_config()


def test_v12_config_migrates_all_the_way(tmp_path, monkeypatch):
    """A pre-refine config loses both retired keys in one load."""
    home = tmp_path / "home"
    config_dir = home / ".vtx"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.yml"
    config_file.write_text(
        "meta:\n  config_version: 12\n\nmode: rlm\n\nrefine:\n  enabled: false\n", encoding="utf-8"
    )

    monkeypatch.setattr(Path, "home", lambda: home)
    reset_config()

    get_config()

    written = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert written["meta"]["config_version"] == CURRENT_CONFIG_VERSION
    assert "mode" not in written
    assert "refine" not in written

    reset_config()

    reset_config()


def test_current_version_config_ignores_a_stale_mode_key(tmp_path, monkeypatch):
    """A config already at the current version keeps loading with a stray key.

    Migration only runs up the version chain, so a ``mode:`` line in a file
    already stamped v16 is not rewritten. It is still inert -- the schema has no
    such field, so it is dropped on read rather than becoming an error.
    """
    home = tmp_path / "home"
    config_dir = home / ".vtx"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.yml"
    config_file.write_text(
        f"meta:\n  config_version: {CURRENT_CONFIG_VERSION}\n\nmode: nonsense\n", encoding="utf-8"
    )

    monkeypatch.setattr(Path, "home", lambda: home)
    reset_config()

    assert get_config().agent.max_turns > 0
    assert not hasattr(get_config(), "mode")

    reset_config()
