"""Skill loading utilities for the SDK.

The SDK shares Vtx's existing skill loader (``vtx.context.skills``) so
that project, user, and built-in skills all work the same way they do
in the TUI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..context.skills import Skill, load_skills

if TYPE_CHECKING:
    pass


def load_vtx_skills(cwd: str | None = None) -> list[Skill]:
    """Load all Vtx-format skills from the project and user scopes.

    Returns a deduplicated list of :class:`vtx.context.skills.Skill`
    objects. Pass the returned skills to :meth:`Agent.set_skills` (or
    inject them into your system prompt manually).
    """
    result = load_skills(cwd=cwd)
    return list(result.skills)


def format_skills_for_prompt(skills: list[Skill]) -> str:
    """Render a list of skills as a compact prompt section.

    Useful for adding to your agent's ``instructions`` if you want the
    LLM to know which skills are available without using the file-based
    skill loader tool.
    """
    if not skills:
        return ""
    lines = ["# Available skills", ""]
    for skill in skills:
        lines.append(f"- {skill.name}: {skill.description}")
    return "\n".join(lines)


__all__ = ["Skill", "format_skills_for_prompt", "load_vtx_skills"]
