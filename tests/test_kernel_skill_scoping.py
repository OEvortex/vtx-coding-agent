"""Kernel-only (Python) skills must not be offered outside RLM mode.

A python skill (``pyproject.toml`` + ``src/<import_name>``) is imported into
the persistent IPython kernel rather than read as instructions. Its SKILL.md is
a REPL API reference — "call ``await compact.run()`` from the REPL" — so
advertising one to a tool-first agent, which has no ``ipython`` tool, is worse
than omitting it: the description reads as generally applicable, the agent
loads the skill, spends context on it, and then has no tool to call.

These lock the filter onto all three discovery surfaces, and the fact that the
``vtx.coding_agent.context.skills`` alias resolves to the same implementation —
it used to be a fork with no python detection at all, so the ``skill`` tool
listed a different set of skills than the prompt it mirrors.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from vtx.ai.agent.context.skills import (
    Skill,
    SkillPythonMetadata,
    formatted_skills,
    is_kernel_skill,
    kernel_skills_available,
    load_skills,
    skills_for_mode,
)
from vtx.ai.agent.prompts.builder import build_system_prompt
from vtx.coding_agent.tools.skill import SkillParams, SkillTool

BUNDLED_KERNEL_SKILLS = {"edit", "compact", "refine", "agent-message", "agent-observe"}


def _write_kernel_skill(root: Path, name: str, *, register_cmd: bool = False) -> None:
    skill_dir = root / ".agents" / "skills" / name
    (skill_dir / "src" / name.replace("-", "_")).mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Kernel-only helper\n"
        f"register_cmd: {str(register_cmd).lower()}\n---\n"
        f"Call from the REPL: `await {name.replace('-', '_')}.run()`\n"
    )
    (skill_dir / "pyproject.toml").write_text(f'[project]\nname = "{name}"\nversion = "0.1.0"\n')
    (skill_dir / "src" / name.replace("-", "_") / "__init__.py").write_text(
        "async def run():\n    pass\n"
    )


def _write_markdown_skill(root: Path, name: str, *, register_cmd: bool = True) -> None:
    skill_dir = root / ".agents" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A normal markdown skill\n"
        f"register_cmd: {str(register_cmd).lower()}\n---\nDo the thing.\n"
    )


def _python_catalog_entries(prompt: str) -> list[str]:
    return re.findall(r"<name>([\w-]+)</name>\s*<type>python</type>", prompt)


def _skills_index_entries(prompt: str) -> list[str]:
    return re.findall(r"^- ([\w-]+) \(python `", prompt, re.M)


class TestSkillClassification:
    def test_python_skill_is_a_kernel_skill(self, tmp_path: Path):
        _write_kernel_skill(tmp_path, "kp")
        skill = load_skills(str(tmp_path)).skills[0]
        assert skill.kind == "python"
        assert is_kernel_skill(skill)

    def test_markdown_skill_is_not_a_kernel_skill(self):
        assert not is_kernel_skill(Skill(path="p", name="n", description="d"))

    def test_kernel_available_only_in_rlm_mode(self):
        assert kernel_skills_available("code_first") is True
        assert kernel_skills_available("tool_first") is False

    def test_kernel_skill_without_metadata_is_not_treated_as_kernel(self):
        """A broken half-detected skill must not vanish from the catalog."""
        assert not is_kernel_skill(Skill(path="p", name="n", description="d", kind="python"))


class TestModeFilter:
    def test_tool_first_drops_kernel_skills(self, tmp_path: Path):
        _write_kernel_skill(tmp_path, "kp")
        _write_markdown_skill(tmp_path, "plain")
        skills = load_skills(str(tmp_path)).skills
        kept = {s.name for s in skills_for_mode(skills, "tool_first")}
        assert kept == {"plain"}

    def test_rlm_keeps_kernel_skills(self, tmp_path: Path):
        _write_kernel_skill(tmp_path, "kp")
        _write_markdown_skill(tmp_path, "plain")
        skills = load_skills(str(tmp_path)).skills
        kept = {s.name for s in skills_for_mode(skills, "code_first")}
        assert kept == {"kp", "plain"}

    def test_filter_does_not_mutate_input(self, tmp_path: Path):
        _write_kernel_skill(tmp_path, "kp")
        _write_markdown_skill(tmp_path, "plain")
        skills = load_skills(str(tmp_path)).skills
        skills_for_mode(skills, "tool_first")
        assert len(skills) == 2

    def test_all_kernel_skills_can_be_filtered(self, tmp_path: Path):
        """Filtering must not leave the list empty-but-truthy behind."""
        _write_kernel_skill(tmp_path, "kp")
        skills = load_skills(str(tmp_path)).skills
        assert skills_for_mode(skills, "tool_first") == []


class TestPromptCatalog:
    def test_tool_first_prompt_has_no_python_skills(self, tmp_path: Path):
        _write_kernel_skill(tmp_path, "kp")
        _write_markdown_skill(tmp_path, "plain")
        skills = load_skills(str(tmp_path)).skills
        with patch("vtx.ai.agent.prompts.builder.vtx_config") as cfg:
            cfg.mode = "tool_first"
            prompt = build_system_prompt(str(tmp_path), skills=skills)
        assert _python_catalog_entries(prompt) == []
        assert "kp" not in prompt
        assert "plain" in prompt

    def test_rlm_prompt_still_indexes_python_skills(self, tmp_path: Path):
        _write_kernel_skill(tmp_path, "kp")
        skills = load_skills(str(tmp_path)).skills
        with patch("vtx.ai.agent.prompts.builder.vtx_config") as cfg:
            cfg.mode = "code_first"
            prompt = build_system_prompt(str(tmp_path), skills=skills)
        assert _skills_index_entries(prompt) == ["kp"]

    def test_prompt_with_only_kernel_skills_omits_the_section(self, tmp_path: Path):
        _write_kernel_skill(tmp_path, "kp")
        skills = load_skills(str(tmp_path)).skills
        with patch("vtx.ai.agent.prompts.builder.vtx_config") as cfg:
            cfg.mode = "tool_first"
            prompt = build_system_prompt(str(tmp_path), skills=skills)
        assert "## Skills (mandatory)" not in prompt

    def test_kernel_note_only_appears_when_python_skills_are_offered(self):
        kernel = Skill(
            path="p",
            name="kp",
            description="d",
            kind="python",
            python=SkillPythonMetadata(import_name="kp", package_path="p", pyproject_path="pp"),
        )
        plain = Skill(path="p", name="plain", description="d")
        with_kernel = formatted_skills([kernel, plain])
        assert "persistent Python kernel" in with_kernel
        assert "persistent Python kernel" not in formatted_skills([plain])


class TestSkillToolDiscovery:
    def _list_names(self, mode: str) -> list[str]:
        with patch("vtx.ai.agent.context.skills.kernel_skills_available") as available:
            available.return_value = mode == "code_first"
            result = asyncio.run(SkillTool().execute(SkillParams(action="list")))
        return [
            line[2:].split(" [")[0] for line in result.result.splitlines() if line.startswith("- ")
        ]

    def test_tool_first_list_hides_bundled_kernel_skills(self):
        listed = set(self._list_names("tool_first"))
        assert BUNDLED_KERNEL_SKILLS.isdisjoint(listed)

    def test_rlm_list_shows_bundled_kernel_skills(self):
        listed = set(self._list_names("code_first"))
        assert listed >= BUNDLED_KERNEL_SKILLS

    def test_list_still_shows_markdown_skills_in_tool_first(self):
        listed = set(self._list_names("tool_first"))
        assert {"review", "init", "github"} <= listed


class TestCodingAgentAlias:
    """The alias was a stale fork: the skill tool disagreed with the prompt."""

    def test_alias_shares_the_canonical_implementation(self):
        import vtx.ai.agent.context.skills as canonical
        import vtx.coding_agent.context.skills as alias

        assert alias.load_skills is canonical.load_skills
        assert alias.formatted_skills is canonical.formatted_skills
        assert alias.Skill is canonical.Skill

    def test_alias_loader_detects_python_skills(self, tmp_path: Path):
        import vtx.coding_agent.context.skills as alias

        _write_kernel_skill(tmp_path, "kp")
        skill = alias.load_skills(str(tmp_path)).skills[0]
        assert skill.kind == "python"

    def test_alias_register_cmd_defaults_to_opt_in(self):
        import vtx.coding_agent.context.skills as alias

        skills = alias.load_builtin_cmd_skills().skills
        assert any(s.name == "review" and s.register_cmd for s in skills)
        # Kernel skills ship no register_cmd, and must not be opt-in by default.
        assert all(not s.register_cmd for s in skills if s.kind == "python")


class TestSlashCommandRegistration:
    def test_kernel_skill_never_registers_as_a_slash_command(self, tmp_path: Path):
        """Opt-in cannot enable a kernel skill: `/name` would inject REPL docs."""
        from vtx.tui.app import Vtx

        _write_kernel_skill(tmp_path, "kp", register_cmd=True)
        _write_markdown_skill(tmp_path, "plain", register_cmd=True)
        captured: list = []

        class _Box:
            def set_commands(self, commands):
                captured.extend(commands)

        class _Runtime:
            agent = None

        app = Vtx.__new__(Vtx)
        app._cwd = str(tmp_path)
        app._runtime = _Runtime()
        app._loaded_extensions = type("E", (), {"all_commands": {}})()
        app.query_one = lambda *a, **k: _Box()

        with patch("vtx.ai.agent.context.skills.load_skills") as load:
            load.return_value = load_skills(str(tmp_path))
            app._sync_slash_commands()

        names = {c.name for c in captured}
        assert "plain" in names
        assert "kp" not in names


class TestSkillToolRunAction:
    """``run`` is the kernel-only action; outside RLM it must refuse, not spawn."""

    def _run(self, name: str, available: bool) -> object:
        kernel_result = SimpleNamespace(result="ok", success=True, ui_summary="ok")
        with (
            patch("vtx.coding_agent.tools.skill.kernel_skills_available", return_value=available),
            patch(
                "vtx.ai.agent.tools.ipython.IpythonTool.execute",
                new=AsyncMock(return_value=kernel_result),
            ) as execute,
        ):
            result = asyncio.run(SkillTool().execute(SkillParams(action="run", name=name)))
        if available:
            execute.assert_awaited_once()
        return result

    def test_tool_first_refuses_without_starting_a_kernel(self):
        result = self._run("refine", available=False)
        assert result.success is False
        assert "RLM mode" in result.result

    def test_rlm_runs_the_skill(self):
        result = self._run("refine", available=True)
        assert result.success is True
