"""Back-compat alias for :mod:`vtx.ai.agent.context.skills`.

This module used to be a hand-maintained fork of the skills loader. It fell
behind: it had no Python-skill detection, no YAML frontmatter parsing, and
still defaulted ``register_cmd`` to ``True`` — so the ``skill`` tool
disagreed with the system prompt it was supposed to mirror, offering skills
that the prompt had already hidden and hiding descriptions the prompt showed
mangled. Every other file in this package (``loader``, ``agent_mds``, ``git``)
is byte-identical to its ``vtx.ai.agent.context`` counterpart, so this alias
completes the set and leaves a single implementation to keep correct.
"""

from __future__ import annotations

from vtx.ai.agent.context.skills import (
    DEFAULT_SKILL_CATEGORY,
    MAX_CATEGORY_LENGTH,
    MAX_CMD_INFO_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    LoadSkillsResult,
    Skill,
    SkillPythonMetadata,
    SkillWarning,
    escape_xml,
    formatted_skills,
    formatted_skills_index,
    get_registered_skills_packages,
    get_user_skills_dir,
    get_vtx_config_dir,
    is_kernel_skill,
    kernel_skills_available,
    load_builtin_cmd_skills,
    load_skills,
    merge_registered_skills,
    register_skills_package,
    render_skill_prompt,
    shorten_path,
    skills_for_mode,
    strip_frontmatter,
    sync_builtin_skills,
    unregister_skills_package,
)
from vtx.ai.agent.context.skills import _find_git_root as _find_git_root
from vtx.ai.agent.context.skills import _load_skill_from_dir as _load_skill_from_dir
from vtx.ai.agent.context.skills import _load_skills_from_dir as _load_skills_from_dir
from vtx.ai.agent.context.skills import _load_skills_recursive as _load_skills_recursive
from vtx.ai.agent.context.skills import _parse_frontmatter as _parse_frontmatter
from vtx.ai.agent.context.skills import _project_skill_dirs as _project_skill_dirs
from vtx.ai.agent.context.skills import _validate_skill as _validate_skill

__all__ = [
    "DEFAULT_SKILL_CATEGORY",
    "MAX_CATEGORY_LENGTH",
    "MAX_CMD_INFO_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_NAME_LENGTH",
    "LoadSkillsResult",
    "Skill",
    "SkillPythonMetadata",
    "SkillWarning",
    "escape_xml",
    "formatted_skills",
    "formatted_skills_index",
    "get_registered_skills_packages",
    "get_user_skills_dir",
    "get_vtx_config_dir",
    "is_kernel_skill",
    "kernel_skills_available",
    "load_builtin_cmd_skills",
    "load_skills",
    "merge_registered_skills",
    "register_skills_package",
    "render_skill_prompt",
    "shorten_path",
    "skills_for_mode",
    "strip_frontmatter",
    "sync_builtin_skills",
    "unregister_skills_package",
]
