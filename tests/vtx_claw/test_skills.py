from __future__ import annotations

from pathlib import Path

from vtx_claw.skills.loader import SkillLoader
from vtx_claw.skills.registry import SkillRegistry


def test_skill_loader_discovers_skill(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: demo skill\n---\n# Demo\n")
    loader = SkillLoader(tmp_path / "skills")
    assert any(s["name"] == "demo" for s in loader.list_metadata())


def test_skill_registry_returns_names(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: demo skill\n---\n# Demo\n")
    reg = SkillRegistry(tmp_path / "skills")
    assert "demo" in reg.list_names()
    demo_skill = reg.get("demo")
    assert demo_skill is not None
    assert demo_skill.description == "demo skill"


def test_skill_search_filters(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "wave"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: wave\ndescription: audio wave skill\n---\n")
    reg = SkillRegistry(tmp_path / "skills")
    hits = reg.search("audio")
    assert len(hits) == 1
    assert hits[0].name == "wave"


def test_skill_install_creates_file(tmp_path: Path):
    reg = SkillRegistry(tmp_path / "skills")
    assert reg.install("newskill")
    target = tmp_path / "skills" / "newskill" / "SKILL.md"
    assert target.exists()
    assert not reg.install("newskill")
