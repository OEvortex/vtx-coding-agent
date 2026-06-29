from __future__ import annotations

from pathlib import Path

import pytest

from vtx_claw.config.schema import PersonaConfig
from vtx_claw.persona import PersonaManager


def test_default_soul_loaded(tmp_path: Path):
    soul = tmp_path / "soul.md"
    soul.write_text("I am a helpful assistant.")
    cfg = PersonaConfig(soul_file=str(soul), persona_file=str(tmp_path / "persona.md"))
    pm = PersonaManager(cfg)
    out = pm.get_system_prompt()
    assert "helpful assistant" in out


def test_persona_switch(tmp_path: Path):
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    default = persona_dir / "default.md"
    default.write_text("Default persona.")
    coder = persona_dir / "coder.md"
    coder.write_text("You are a coder.")
    cfg = PersonaConfig(soul_file=str(tmp_path / "soul.md"), persona_file=str(coder))
    pm = PersonaManager(cfg)
    pm.set_active("default")
    assert "Default persona." in pm.get_system_prompt()


def test_active_name_default(tmp_path: Path):
    cfg = PersonaConfig()
    pm = PersonaManager(cfg)
    assert pm.active_name() == "default"


def test_unknown_persona_raises(tmp_path: Path):
    cfg = PersonaConfig()
    pm = PersonaManager(cfg)
    with pytest.raises(ValueError):
        pm.set_active("nonexistent")
