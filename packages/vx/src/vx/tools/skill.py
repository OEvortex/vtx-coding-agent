"""Load a skill's instructions: the tool the ``<available_skills>`` catalog names.

ctx assembles that catalog into the system prompt and tells the model to load a
matching entry with ``skill(action="load", ...)``. vx has to ship that tool, or
the prompt advertises something the model cannot call — and because the catalog
carries no paths, ``read`` cannot stand in for it.

Choosing, not managing: one skill, by name, exactly as the catalog spells it.
Creating or editing a skill is a file edit, which is what apply_patch is for.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ctx.agent.context.skills import (
    Skill,
    load_builtin_cmd_skills,
    load_skills,
    merge_registered_skills,
    strip_frontmatter,
)
from ctx.agent.tools.base import BaseTool, ToolResult
from pydantic import BaseModel, Field

#: Files beside SKILL.md worth naming back to the model, and no more.
MAX_SIBLINGS = 10


class SkillParams(BaseModel):
    name: str = Field(description="Skill name, as listed in <available_skills>")
    file: str | None = Field(
        description="A file inside the skill, instead of its SKILL.md", default=None
    )


def find_skill(name: str, cwd: str) -> Skill | None:
    """The loadable skill by that name: project and global first, then bundled."""
    project = load_skills(cwd).skills
    bundled = load_builtin_cmd_skills().skills
    for skill in merge_registered_skills(project, bundled):
        if skill.name == name:
            return skill
    return None


def _siblings(skill_path: Path) -> list[str]:
    """Files in the skill's directory, so a skill's scripts are discoverable."""
    try:
        found = sorted(
            path.relative_to(skill_path.parent).as_posix()
            for path in skill_path.parent.rglob("*")
            if path.is_file() and path != skill_path and "__pycache__" not in path.parts
        )
    except OSError:
        return []
    return found[:MAX_SIBLINGS]


class SkillTool(BaseTool):
    name = "skill"
    tool_icon = "⚙"
    params = SkillParams
    description = (
        "Load a skill's instructions into context: the name from <available_skills>, "
        "optionally a file inside the skill instead of its SKILL.md."
    )

    def format_call(self, params: SkillParams) -> str:
        return params.name

    async def execute(
        self, params: SkillParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        name = params.name.strip()
        skill = find_skill(name, str(Path.cwd()))
        if skill is None:
            return ToolResult(success=False, result=f"No skill named {name!r}.")

        skill_file = Path(skill.path)
        if params.file:
            skill_file = skill_file.parent / params.file
        try:
            body = strip_frontmatter(skill_file.read_text(encoding="utf-8"))
        except OSError as exc:
            return ToolResult(success=False, result=f"Could not read {skill_file.name}: {exc}")

        lines = [
            f'<skill_content name="{skill.name}">',
            "",
            body.strip(),
            "",
            f"Base directory for this skill: {skill_file.parent}",
            "Relative paths in this skill are relative to that directory, not the working one.",
        ]
        siblings = _siblings(Path(skill.path))
        if siblings:
            lines += ["", "Files in this skill directory:"]
            lines += [f"- {name}" for name in siblings]
        lines.append("</skill_content>")
        return ToolResult(success=True, result="\n".join(lines))
