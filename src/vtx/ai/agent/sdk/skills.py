"""Skill loading utilities for the SDK.

The SDK shares Vtx's existing skill loader so that project, user, and
built-in skills all work the same way they do in the coding agent's TUI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vtx.ai.agent.skills import Skill


def load_vtx_skills(cwd: str | None = None) -> list[Skill]:
    """Load all Vtx-format skills from the project and user scopes.

    Returns a deduplicated list of :class:`vtx.ai.agent.skills.Skill`
    objects. Pass the returned skills to :meth:`Agent.set_skills` (or
    inject them into your system prompt manually).
    """
    from vtx.ai.agent.skills import load_skills

    result = load_skills(cwd)
    return result.skills


def load_builtin_vtx_skills() -> list[Skill]:
    """Load built-in skills bundled with the Vtx distribution.

    These are the fallback skills (e.g. ``coding``, ``vtx-config``) that
    ship with the package.
    """
    from vtx.ai.agent.skills import load_builtin_cmd_skills

    result = load_builtin_cmd_skills()
    return result.skills


__all__ = ["load_builtin_vtx_skills", "load_vtx_skills"]
