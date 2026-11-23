"""System prompt package for Ctx — assembly mechanism only.

Public surface: :func:`build_system_prompt` composes the base identity plus
whatever sections are registered in :mod:`ctx.agent.prompts.registry`. The
persona and product-specific content live in the product package
(``ctx.code.prompts``), not here.
"""

from .builder import build_system_prompt
from .env import ENV_HEADER, build_env_section
from .registry import (
    PromptEnv,
    PromptSection,
    base_prompt,
    prompt_sections,
    register_base_prompt,
    register_prompt_section,
    unregister_prompt_section,
)
from .tooling import TOOL_USAGE_HEADER, build_tool_guidelines_section

__all__ = [
    "ENV_HEADER",
    "TOOL_USAGE_HEADER",
    "PromptEnv",
    "PromptSection",
    "base_prompt",
    "build_env_section",
    "build_system_prompt",
    "build_tool_guidelines_section",
    "prompt_sections",
    "register_base_prompt",
    "register_prompt_section",
    "unregister_prompt_section",
]
