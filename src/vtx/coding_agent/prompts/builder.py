"""System prompt assembly for Vtx.

Re-exported from :mod:`vtx.ai.agent.prompts.builder` with the
``vtx.coding_agent`` config object so that the coding-agent layer sees
its own configuration values while sharing the implementation.
"""

from __future__ import annotations

from typing import Any

from vtx.coding_agent.config import config as vtx_config

__all__ = ["build_system_prompt"]


def build_system_prompt(
    cwd: str,
    context: Any = None,
    tools: list[Any] | None = None,
    *,
    base_content: str | None = None,
    include_git_context: bool | None = None,
    include_ponytail: bool | None = None,
    extra_instructions: str | None = None,
    extra_instructions_mode: str = "append",
    skills: list[Any] | None = None,
) -> str:
    """Compose the final system prompt for the agent.

    This wrapper ensures the coding-agent config is active when the
    underlying builder checks ``mode`` and other settings.
    """
    import vtx.ai.agent.prompts.builder as _builder

    original_config = _builder.vtx_config
    _builder.vtx_config = vtx_config
    try:
        return _builder.build_system_prompt(
            cwd=cwd,
            context=context,
            tools=tools,
            base_content=base_content,
            include_git_context=include_git_context,
            include_ponytail=include_ponytail,
            extra_instructions=extra_instructions,
            extra_instructions_mode=extra_instructions_mode,
            skills=skills,
        )
    finally:
        _builder.vtx_config = original_config
