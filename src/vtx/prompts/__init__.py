"""System prompt package for Vtx.

Public surface mirrors the JARVIS ``core/agents/prompts`` layout:
composable string constants in :mod:`vtx.prompts.identity`, per-section
builders in :mod:`vtx.prompts.tooling` and :mod:`vtx.prompts.env`, and
the orchestrator in :mod:`vtx.prompts.builder`.
"""

from .builder import build_system_prompt
from .env import ENV_HEADER, build_env_section
from .identity import (
    CONTEXT_AWARENESS,
    DEFAULT_VTX_BASE,
    EDITING_CONSTRAINTS,
    ERROR_RECOVERY,
    EXECUTION_DISCIPLINE,
    OUTPUT_FORMATTING,
    PROGRESS_UPDATES,
    SAFETY,
    TASK_COMPLETION,
    TOOL_USE_ENFORCEMENT,
    VTX_GENERAL_RULES,
    VTX_IDENTITY,
)
from .tooling import TOOL_USAGE_HEADER, build_tool_guidelines_section

__all__ = [
    "CONTEXT_AWARENESS",
    "DEFAULT_VTX_BASE",
    "EDITING_CONSTRAINTS",
    "ENV_HEADER",
    "ERROR_RECOVERY",
    "EXECUTION_DISCIPLINE",
    "OUTPUT_FORMATTING",
    "PROGRESS_UPDATES",
    "SAFETY",
    "TASK_COMPLETION",
    "TOOL_USAGE_HEADER",
    "TOOL_USE_ENFORCEMENT",
    "VTX_GENERAL_RULES",
    "VTX_IDENTITY",
    "build_env_section",
    "build_system_prompt",
    "build_tool_guidelines_section",
]
