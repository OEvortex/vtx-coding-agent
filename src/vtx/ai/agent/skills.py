"""Skills discovery, registration, and loading for vtx.

Re-exports all skills APIs and data structures from :mod:`vtx.ai.agent.context.skills`.
"""

from __future__ import annotations

from vtx.ai.agent.context.skills import (  # noqa: F401
    Any,
    DEFAULT_SKILL_CATEGORY,
    LoadSkillsResult,
    MAX_CATEGORY_LENGTH,
    MAX_CMD_INFO_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    Path,
    Skill,
    SkillWarning,
    dataclass,
    escape_xml,
    formatted_skills,
    get_registered_skills_packages,
    get_user_skills_dir,
    get_vtx_config_dir,
    load_builtin_cmd_skills,
    load_skills,
    merge_registered_skills,
    register_skills_package,
    render_skill_prompt,
    shorten_path,
    strip_frontmatter,
    sync_builtin_skills,
    unregister_skills_package,
)

__all__ = [
    "Any",
    "DEFAULT_SKILL_CATEGORY",
    "LoadSkillsResult",
    "MAX_CATEGORY_LENGTH",
    "MAX_CMD_INFO_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_NAME_LENGTH",
    "Path",
    "Skill",
    "SkillWarning",
    "dataclass",
    "escape_xml",
    "formatted_skills",
    "get_registered_skills_packages",
    "get_user_skills_dir",
    "get_vtx_config_dir",
    "load_builtin_cmd_skills",
    "load_skills",
    "merge_registered_skills",
    "register_skills_package",
    "render_skill_prompt",
    "shorten_path",
    "strip_frontmatter",
    "sync_builtin_skills",
    "unregister_skills_package",
]
