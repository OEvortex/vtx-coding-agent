"""System prompt assembly for VTX Claw.

The composer joins a small set of named sections in a fixed order:

1. **base**    - the agent identity + general rules
2. **tooling** - ``# Tool usage`` lines aggregated from tool guidelines
3. **project** - discovered ``AGENTS.md`` / ``CLAUDE.md`` files
4. **skills**  - discovered skill descriptions
5. **env**     - current date/time and working directory

Each section is empty when its source has nothing to contribute.
"""

from __future__ import annotations

from typing import Any

from vtx.context import Context, formatted_agent_mds, formatted_skills
from vtx.prompts.env import build_env_section
from vtx.prompts.tooling import build_tool_guidelines_section

from .. import config as claw_config
from .identity import DEFAULT_CLAW_BASE


def _resolve_base(override: str | None) -> str:
    """Pick the user override, the config value, or the default."""
    if override is not None:
        return override
    configured = claw_config.llm.system_prompt.content
    return configured if configured else DEFAULT_CLAW_BASE


def build_system_prompt(
    cwd: str,
    context: Context | None = None,
    tools: list[Any] | None = None,
    *,
    base_content: str | None = None,
    include_git_context: bool | None = None,
    extra_instructions: str | None = None,
    extra_instructions_mode: str = "append",
    skills: list[Any] | None = None,
) -> str:
    """Compose the final system prompt for the Claw agent."""
    if context is None:
        context = Context.load(cwd)

    base = _resolve_base(base_content)
    if extra_instructions and extra_instructions_mode == "replace":
        base = extra_instructions
    sections: list[str] = [base]

    if extra_instructions and extra_instructions_mode == "append":
        sections.append(extra_instructions)

    tool_section = build_tool_guidelines_section(tools)
    if tool_section:
        sections.append(tool_section)

    if context.agents_files:
        sections.append(formatted_agent_mds(context.agents_files))

    effective_skills = skills if skills is not None else context.skills
    if effective_skills:
        sections.append(formatted_skills(effective_skills))

    sections.append(build_env_section(cwd))

    return "\n\n".join(sections)


__all__ = ["build_system_prompt"]
