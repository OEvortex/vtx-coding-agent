"""System prompt package for Vtx.

Re-exports system prompt builder and sections from :mod:`vtx.ai.agent.prompts`.
"""

from __future__ import annotations

from vtx.ai.agent.prompts import (  # noqa: F401
    BACKGROUND_TASKS,
    CONTEXT_AWARENESS,
    DEFAULT_VTX_BASE,
    EDITING_CONSTRAINTS,
    ENV_HEADER,
    ERROR_RECOVERY,
    EXECUTION_DISCIPLINE,
    OUTPUT_FORMATTING,
    PONYTAIL_PROMPT,
    PROGRESS_UPDATES,
    SAFETY,
    TASK_COMPLETION,
    TOOL_USAGE_HEADER,
    TOOL_USE_ENFORCEMENT,
    VTX_GENERAL_RULES,
    VTX_IDENTITY,
    build_env_section,
    build_ponytail_section,
    build_system_prompt,
    build_tool_guidelines_section,
    is_deactivation_command,
)

__all__ = [
    "BACKGROUND_TASKS",
    "CONTEXT_AWARENESS",
    "DEFAULT_VTX_BASE",
    "EDITING_CONSTRAINTS",
    "ENV_HEADER",
    "ERROR_RECOVERY",
    "EXECUTION_DISCIPLINE",
    "OUTPUT_FORMATTING",
    "PONYTAIL_PROMPT",
    "PROGRESS_UPDATES",
    "SAFETY",
    "TASK_COMPLETION",
    "TOOL_USAGE_HEADER",
    "TOOL_USE_ENFORCEMENT",
    "VTX_GENERAL_RULES",
    "VTX_IDENTITY",
    "build_env_section",
    "build_ponytail_section",
    "build_system_prompt",
    "build_tool_guidelines_section",
    "is_deactivation_command",
]
