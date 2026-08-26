"""Skill loading utilities for the SDK.

The SDK shares Vtx's existing skill loader so that project, user, and
built-in skills all work the same way they do in the coding agent's TUI.
The loader lives in the coding-agent layer
(:mod:`vtx.coding_agent.context.skills`) and is imported lazily to keep
the harness import-graph clean.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vtx.coding_agent.context.skills import Skill


def load_vtx_skills(cwd: str | None = None) -> list[Skill]:
    """Load all Vtx-format skills from the project and user scopes.

    Returns a deduplicated list of :class:`vtx.coding_agent.context.skills.Skill`
    objects. Pass the returned skills to :meth:`Agent.set_skills` (or
    inject them into your system prompt manually).
    """
    from vtx.coding_agent.context.skills import load_skills

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
