"""System prompt package for Ctx.

Public surface mirrors the JARVIS ``core/agents/prompts`` layout:
composable string constants in :mod:`ctx.prompts.identity`, per-section
builders in :mod:`ctx.prompts.tooling` and :mod:`ctx.prompts.env`, and
the orchestrator in :mod:`ctx.prompts.builder`.
"""

from .builder import build_system_prompt
from .env import ENV_HEADER, build_env_section
from .identity import (
    BACKGROUND_TASKS,
    CONTEXT_AWARENESS,
    CTX_GENERAL_RULES,
    CTX_IDENTITY,
    DEFAULT_CTX_BASE,
    EDITING_CONSTRAINTS,
    ERROR_RECOVERY,
    EXECUTION_DISCIPLINE,
    OUTPUT_FORMATTING,
    PROGRESS_UPDATES,
    SAFETY,
    TASK_COMPLETION,
    TOOL_USE_ENFORCEMENT,
)
from .ponytail import PONYTAIL_PROMPT, build_ponytail_section, is_deactivation_command
from .tooling import TOOL_USAGE_HEADER, build_tool_guidelines_section

__all__ = [
    "BACKGROUND_TASKS",
    "CONTEXT_AWARENESS",
    "CTX_GENERAL_RULES",
    "CTX_IDENTITY",
    "DEFAULT_CTX_BASE",
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
    "build_env_section",
    "build_ponytail_section",
    "build_system_prompt",
    "build_tool_guidelines_section",
    "is_deactivation_command",
]
