"""Built-in skill discovery for openjarvis — isolated from VTX builtins."""

from __future__ import annotations

from pathlib import Path

from vtx.coding_agent.context.skills import (
    LoadSkillsResult,
    Skill,
    _load_skills_from_dir,
    _load_skills_recursive,
    _project_skill_dirs,
)
from vtx.core.paths import get_agents_dir as get_user_skills_dir

# Bundled skills ship inside the openjarvis package; the filesystem tool
# grants read access to this directory so the agent can consult SKILL.md files.
BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

__all__ = ["BUILTIN_SKILLS_DIR", "filter_bundled_skills", "load_openjarvis_skills"]


def filter_bundled_skills(skills: list[Skill]) -> list[Skill]:
    """Drop VTX bundled skills (``skill.bundled == True``)."""
    return [s for s in skills if not s.bundled]


def load_openjarvis_skills(cwd: str | None = None) -> LoadSkillsResult:
    """Load skills visible to OpenJarvis — *without* VTX builtins.

    Discovery (isolated from VTX):
    1. <cwd-or-ancestor>/.agents/skills/  — project skills
    2. ~/.agents/skills/                  — user global skills
    3. <openjarvis>/agent/skills/         — openjarvis-own bundled skills (if dir exists)

    Explicitly **excludes**:
    - ``~/.vtx/skills/`` (VTX synced builtins)
    - ``vtx.coding_agent.builtin_skills`` package resource (``load_builtin_cmd_skills``)

    This ensures OpenJarvis never sees VTX builtin_skills like
    ``modal``, ``google-colab``, ``review``, ``github``, ``skill-builder``, etc.
    """
    from pathlib import Path as _Path

    resolved_cwd = _Path(cwd) if cwd else _Path.cwd()
    resolved_cwd = resolved_cwd.resolve()

    skill_map: dict[str, Skill] = {}
    all_warnings: list = []

    def add(result: LoadSkillsResult) -> None:
        all_warnings.extend(result.warnings)
        for skill in result.skills:
            if skill.name not in skill_map:
                skill_map[skill.name] = skill
            else:
                # Keep first-seen (project > global > openjarvis-bundled)
                all_warnings.append(
                    type(result.warnings[0])(
                        skill.path,
                        f'name collision: "{skill.name}" already loaded from {skill_map[skill.name].path}',
                    )
                    if result.warnings
                    else type("W", (), {"path": skill.path, "message": ""})()
                )

    # Project + user global only — no VTX synced dir.
    for skills_dir in _project_skill_dirs(resolved_cwd):
        add(_load_skills_from_dir(skills_dir))

    user_skills_dir = (get_user_skills_dir() / "skills").resolve(strict=False)
    if user_skills_dir not in _project_skill_dirs(resolved_cwd):
        add(_load_skills_from_dir(user_skills_dir))

    # OpenJarvis-own bundled skills (opt-in, isolated)
    if BUILTIN_SKILLS_DIR.is_dir():
        result = _load_skills_recursive(BUILTIN_SKILLS_DIR)
        for skill in result.skills:
            skill.bundled = True
        add(result)

    return LoadSkillsResult(skills=list(skill_map.values()), warnings=all_warnings)
