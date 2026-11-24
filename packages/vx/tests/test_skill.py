"""Skill tool: lookup, file reads and the <skill_content> envelope."""

import pytest
from ctx.agent.context.skills import Skill
from ctx.protocol.types import ToolResult
from vx.tools import skill as skill_mod
from vx.tools.skill import SkillParams, SkillTool, find_skill


@pytest.fixture
def skill_dir(tmp_path, monkeypatch):
    d = tmp_path / "skills" / "demo"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: demo\ndescription: test skill\n---\nDo the thing.\n")
    (d / "helper.py").write_text("# helper\n")
    project = [Skill(path=str(d / "SKILL.md"), name="demo", description="test skill")]
    monkeypatch.setattr(
        skill_mod, "load_skills", lambda cwd=None: type("R", (), {"skills": project})()
    )
    monkeypatch.setattr(
        skill_mod, "load_builtin_cmd_skills", lambda *a, **k: type("R", (), {"skills": []})()
    )
    monkeypatch.chdir(tmp_path)
    return d


class TestFindSkill:
    def test_finds_by_name(self, skill_dir):
        skill = find_skill("demo", ".")
        assert skill is not None
        assert skill.name == "demo"

    def test_unknown_returns_none(self, skill_dir):
        assert find_skill("nope", ".") is None


class TestSiblings:
    def test_lists_files_beside_skill_md(self, skill_dir):
        sibs = skill_mod._siblings(skill_dir / "SKILL.md")
        assert "helper.py" in sibs
        assert "SKILL.md" not in sibs


class TestExecute:
    @pytest.mark.asyncio
    async def test_loads_skill_body(self, skill_dir):
        result = await SkillTool().execute(SkillParams(name="demo"))
        assert isinstance(result, ToolResult)
        assert result.success
        text = result.result or ""
        assert "Do the thing." in text
        assert "<skill_content" in text
        assert "helper.py" in text

    @pytest.mark.asyncio
    async def test_missing_skill_fails(self, skill_dir):
        result = await SkillTool().execute(SkillParams(name="ghost"))
        assert not result.success
        assert "ghost" in (result.result or "")

    @pytest.mark.asyncio
    async def test_file_param_reads_sibling(self, skill_dir):
        result = await SkillTool().execute(SkillParams(name="demo", file="helper.py"))
        assert result.success
        assert "# helper" in (result.result or "")

    @pytest.mark.asyncio
    async def test_missing_file_fails(self, skill_dir):
        result = await SkillTool().execute(SkillParams(name="demo", file="nope.py"))
        assert not result.success
