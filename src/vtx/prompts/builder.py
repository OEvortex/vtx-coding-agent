"""System prompt assembly for Vtx.

The composer joins a small set of named sections in a fixed order:

1. **base**    - the agent identity + general rules (or a user override)
2. **tooling** - ``# Tool usage`` lines aggregated from tool guidelines
3. **project** - discovered ``AGENTS.md`` / ``CLAUDE.md`` files
4. **skills**  - discovered skill descriptions
5. **git**     - snapshot of the working tree (only when enabled)
6. **env**     - current date/time and working directory

Each section is empty when its source has nothing to contribute, so
the final prompt is just whatever joined list comes back. ``build_system_prompt``
is the single entry point used by :mod:`vtx.loop` and the runtime.
"""

from __future__ import annotations

from .. import config as vtx_config
from ..context import Context, formatted_agent_mds, formatted_git_context, formatted_skills
from ..tools import BaseTool
from .env import build_env_section
from .identity import DEFAULT_VTX_BASE
from .tooling import build_tool_guidelines_section


def _resolve_base(override: str | None) -> str:
    """Pick the user override, the config value, or the Python default."""
    if override is not None:
        return override
    configured = vtx_config.llm.system_prompt.content
    return configured if configured else DEFAULT_VTX_BASE


def _resolve_git_flag(include_git: bool | None) -> bool:
    if include_git is not None:
        return include_git
    return vtx_config.llm.system_prompt.git_context


def build_system_prompt(
    cwd: str,
    context: Context | None = None,
    tools: list[BaseTool] | None = None,
    *,
    base_content: str | None = None,
    include_git_context: bool | None = None,
) -> str:
    """Compose the final system prompt for the agent.

    Args:
        cwd: Working directory used for context discovery and the env line.
        context: Pre-loaded :class:`Context`. Loaded from ``cwd`` when omitted.
        tools: Active tool set; contributes the ``# Tool usage`` section.
        base_content: Override for the base identity/rules string. When
            ``None`` the function falls back to ``vtx_config.llm.system_prompt.content``
            and finally :data:`vtx.prompts.identity.DEFAULT_VTX_BASE`.
        include_git_context: Force the git section on/off. When ``None``
            the value is read from config.
    """
    if context is None:
        context = Context.load(cwd)

    sections: list[str] = [_resolve_base(base_content)]

    tool_section = build_tool_guidelines_section(tools)
    if tool_section:
        sections.append(tool_section)

    if context.agents_files:
        sections.append(formatted_agent_mds(context.agents_files))

    if context.skills:
        sections.append(formatted_skills(context.skills))

    if _resolve_git_flag(include_git_context):
        git_section = formatted_git_context(cwd)
        if git_section:
            sections.append(git_section)

    sections.append(build_env_section(cwd))

    return "\n\n".join(sections)


__all__ = ["build_system_prompt"]
