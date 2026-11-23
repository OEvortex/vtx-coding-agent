"""The coding agent's prompt content, registered into the harness builder.

``ctx.agent.prompts`` owns prompt *assembly*; this package owns the coding
product's *content*: the base persona and ponytail mode. Registration runs
eagerly at ``ctx.coding_agent`` import — the same slot
``register_builtin_tool_rows`` already uses.
"""

from __future__ import annotations

from ctx.agent.prompts.registry import PromptEnv, register_base_prompt, register_prompt_section

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
    _compose_default_base,
)
from .ponytail import PONYTAIL_PROMPT, build_ponytail_section, is_deactivation_command
from .todo import build_todo_section

__all__ = [
    "BACKGROUND_TASKS",
    "CONTEXT_AWARENESS",
    "CTX_GENERAL_RULES",
    "CTX_IDENTITY",
    "DEFAULT_CTX_BASE",
    "EDITING_CONSTRAINTS",
    "ERROR_RECOVERY",
    "EXECUTION_DISCIPLINE",
    "OUTPUT_FORMATTING",
    "PONYTAIL_PROMPT",
    "PROGRESS_UPDATES",
    "SAFETY",
    "TASK_COMPLETION",
    "TOOL_USE_ENFORCEMENT",
    "_compose_default_base",
    "build_ponytail_section",
    "build_todo_section",
    "is_deactivation_command",
    "register",
]


def _ponytail_section(env: PromptEnv) -> str | None:
    return PONYTAIL_PROMPT if env.include_ponytail else None


def register() -> None:
    """Register the coding persona and ponytail section (idempotent)."""
    register_base_prompt(lambda: DEFAULT_CTX_BASE)
    register_prompt_section("ponytail", _ponytail_section, order=15)


register()
