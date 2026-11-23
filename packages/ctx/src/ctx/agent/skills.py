"""Skills discovery, registration, and loading for ctx.

Re-exports all skills APIs and data structures from :mod:`ctx.agent.context.skills`.
"""

from __future__ import annotations

from ctx.agent.context.skills import (
    DEFAULT_SKILL_CATEGORY,
    MAX_CATEGORY_LENGTH,
    MAX_CMD_INFO_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    Any,
    LoadSkillsResult,
    Path,
    Skill,
    SkillWarning,
    dataclass,
    escape_xml,
    formatted_skills,
    get_ctx_config_dir,
    get_registered_skills_packages,
    get_user_skills_dir,
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
    "DEFAULT_SKILL_CATEGORY",
    "MAX_CATEGORY_LENGTH",
    "MAX_CMD_INFO_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_NAME_LENGTH",
    "Any",
    "LoadSkillsResult",
    "Path",
    "Skill",
    "SkillWarning",
    "dataclass",
    "escape_xml",
    "formatted_skills",
    "get_ctx_config_dir",
    "get_registered_skills_packages",
    "get_user_skills_dir",
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
