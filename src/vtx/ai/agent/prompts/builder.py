"""System prompt assembly for Vtx.

The composer joins a small set of named sections in a fixed order:

1. **base**    - the agent identity + general rules (or a user override)
2. **harness** - ``# Continual Harness State`` digest (both runtime modes)
3. **tooling** - ``# Tool usage`` lines aggregated from tool guidelines
4. **project** - discovered ``AGENTS.md`` / ``CLAUDE.md`` files
5. **skills**  - discovered skill descriptions
6. **git**     - snapshot of the working tree (only when enabled)
7. **env**     - current date/time and working directory

Each section is empty when its source has nothing to contribute, so
the final prompt is just whatever joined list comes back. ``build_system_prompt``
is the single entry point used by :mod:`vtx.loop` and the runtime.
"""

from __future__ import annotations

import logging
from typing import Any

from vtx.ai.agent.context import (
    Context,
    formatted_agent_mds,
    formatted_git_context,
    formatted_skills,
    formatted_skills_index,
    skills_for_mode,
)
from vtx.ai.agent.tools import BaseTool
from vtx.ai.config import config as vtx_config

from .env import build_env_section
from .identity import DEFAULT_VTX_BASE
from .ponytail import build_ponytail_section
from .tooling import build_tool_guidelines_section


def _resolve_base(override: str | None) -> str:
    """Return the hardcoded base identity prompt."""
    if override is not None:
        return override
    return DEFAULT_VTX_BASE


def _resolve_git_flag(include_git: bool | None) -> bool:
    if include_git is not None:
        return include_git
    return vtx_config.llm.system_prompt.git_context


def _resolve_ponytail_flag(include_ponytail: bool | None) -> bool:
    if include_ponytail is not None:
        return include_ponytail
    return getattr(vtx_config.llm.system_prompt, "ponytail", False)


def _harness_digest_section(cwd: str, mode: str) -> str:
    """Continual-harness digest (prime parity), rendered in every mode.

    Entries + recent refinements, omitted entirely when there is nothing to
    show. ``mode`` only picks the call contract the model is told to use
    (the REPL-native forms in ``code_first``, the tool-first forms in
    ``tool_first``). A rendering failure must never break prompt assembly.
    """
    try:
        from vtx.ai.agent.rlm.refine import harness_digest_for_prompt
        from vtx.ai.agent.rlm.registry import bridge_session_id

        return harness_digest_for_prompt(bridge_session_id(), cwd, mode=mode)
    except Exception:
        logging.getLogger(__name__).exception("harness digest build failed")
        return ""


def build_system_prompt(
    cwd: str,
    context: Context | None = None,
    tools: list[BaseTool] | None = None,
    *,
    base_content: str | None = None,
    include_git_context: bool | None = None,
    include_ponytail: bool | None = None,
    extra_instructions: str | None = None,
    extra_instructions_mode: str = "append",
    skills: list[Any] | None = None,
) -> str:
    """Compose the final system prompt for the agent.

    Args:
        cwd: Working directory used for context discovery and the env line.
        context: Pre-loaded :class:`Context`. Loaded from ``cwd`` when omitted.
        tools: Active tool set; contributes the ``# Tool usage`` section.
        base_content: Override for the base identity/rules string. When
            ``None`` the function uses :data:`vtx.prompts.identity.DEFAULT_VTX_BASE`.
        include_git_context: Force the git section on/off. When ``None``
            the value is read from config.
        extra_instructions: Optional extra instructions appended to (or
            replacing) the base identity block. Used by switchable agents
            to inject their per-agent profile. ``None`` or empty string
            means "no extra section".
        extra_instructions_mode: ``"append"`` (default) inserts the extra
            block after the base identity; ``"replace"`` swaps the base
            identity out entirely. Ignored when ``extra_instructions`` is
            empty.
    """
    if context is None:
        context = Context.load(cwd)

    mode = getattr(vtx_config, "mode", "tool_first")
    if mode == "code_first" and base_content is None:
        from vtx.coding_agent.prompts.rlm import build_rlm_system_prompt

        installed_skills = (
            [s.name for s in (skills or context.skills)] if (skills or context.skills) else []
        )
        tool_names = [t.name if hasattr(t, "name") else str(t) for t in (tools or [])]
        base = build_rlm_system_prompt(
            cwd=cwd, installed_skills=installed_skills, active_tools=tool_names or ["ipython"]
        )
        sections: list[str] = [base]
        # Continual-harness digest (prime parity): entries + recent refinements,
        # omitted entirely when there is nothing to show.
        digest = _harness_digest_section(cwd, mode)
        if digest:
            sections.append(digest)
        if extra_instructions and extra_instructions_mode == "append":
            sections.append(extra_instructions)
        tool_section = build_tool_guidelines_section(tools)
        if tool_section:
            sections.append(tool_section)
        if context.agents_files:
            sections.append(formatted_agent_mds(context.agents_files))
        effective_skills = skills if skills is not None else context.skills
        if effective_skills:
            # RLM mode gets the compact routing index: the base prompt already
            # documents the pre-imported modules, and the model reads SKILL.md
            # on demand. The full catalog costs ~6k tokens every turn.
            sections.append(formatted_skills_index(effective_skills))
        if _resolve_git_flag(include_git_context):
            git_section = formatted_git_context(cwd)
            if git_section:
                sections.append(git_section)
        sections.append(build_env_section(cwd))
        return "\n\n".join(sections)

    base = _resolve_base(base_content)
    if extra_instructions and extra_instructions_mode == "replace":
        base = extra_instructions
    sections: list[str] = [base]

    # Continual-harness digest (prime parity). The harness is mode-neutral:
    # prompt notes, memories, skills, and subagent specs are rendered in the
    # tool-first prompt too, so refinement changes what the model actually
    # reads. Only the call contract inside the digest differs per mode.
    digest = _harness_digest_section(cwd, mode)
    if digest:
        sections.append(digest)

    if extra_instructions and extra_instructions_mode == "append":
        sections.append(extra_instructions)

    if _resolve_ponytail_flag(include_ponytail):
        sections.append(build_ponytail_section())

    tool_section = build_tool_guidelines_section(tools)
    if tool_section:
        sections.append(tool_section)

    if context.agents_files:
        sections.append(formatted_agent_mds(context.agents_files))

    effective_skills = skills if skills is not None else context.skills
    if effective_skills:
        # No kernel in tool-first mode, so python skills are unrunnable here.
        prompt_skills = skills_for_mode(effective_skills, mode)
        if prompt_skills:
            sections.append(formatted_skills(prompt_skills))

    if _resolve_git_flag(include_git_context):
        git_section = formatted_git_context(cwd)
        if git_section:
            sections.append(git_section)

    sections.append(build_env_section(cwd))

    return "\n\n".join(sections)


__all__ = ["build_system_prompt"]
