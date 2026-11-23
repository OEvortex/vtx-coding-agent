"""System prompt assembly for Ctx.

The composer joins the base identity with registered prompt sections in
order. ``ctx.agent.prompts`` owns only the mechanism: the persona and any
product-specific sections are contributed through
:mod:`ctx.agent.prompts.registry` — the coding agent registers its identity,
ponytail mode, and friends from ``ctx.code.prompts``.

Built-in sections (tooling, project files, skills, git, env) are registered
below at import time; products may ``replace=True`` any of them or add their
own via :func:`register_prompt_section`.
"""

from __future__ import annotations

from typing import Any

from ctx.agent.context import Context, formatted_agent_mds, formatted_git_context, formatted_skills
from ctx.agent.tools import BaseTool
from ctx.core.config import config as ctx_config

from .env import build_env_section
from .registry import PromptEnv, base_prompt, prompt_sections, register_prompt_section
from .tooling import build_tool_guidelines_section


def _resolve_base(override: str | None) -> str:
    """Caller override > extra-instructions replace > registered provider."""
    if override is not None:
        return override
    return base_prompt()


def _resolve_git_flag(include_git: bool | None) -> bool:
    if include_git is not None:
        return include_git
    return ctx_config.llm.system_prompt.git_context


def _resolve_ponytail_flag(include_ponytail: bool | None) -> bool:
    if include_ponytail is not None:
        return include_ponytail
    return getattr(ctx_config.llm.system_prompt, "ponytail", False)


def _section_project(env: PromptEnv) -> str | None:
    if env.context is not None and env.context.agents_files:
        return formatted_agent_mds(env.context.agents_files)
    return None


def _section_skills(env: PromptEnv) -> str | None:
    effective = (
        env.skills if env.skills is not None else (env.context.skills if env.context else [])
    )
    return formatted_skills(effective) if effective else None


def _section_git(env: PromptEnv) -> str | None:
    if not env.include_git_context:
        return None
    return formatted_git_context(env.cwd) or None


def _section_env(env: PromptEnv) -> str:
    return build_env_section(env.cwd)


# Built-in sections, in prompt order. Products slot in via ``order``.
register_prompt_section(
    "tooling", lambda env: build_tool_guidelines_section(env.tools) or None, order=20
)
register_prompt_section("project", _section_project, order=30)
register_prompt_section("skills", _section_skills, order=40)
register_prompt_section("git", _section_git, order=50)
register_prompt_section("env", _section_env, order=90)


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
            ``None`` the registered base-prompt provider is used.
        include_git_context: Force the git section on/off. When ``None``
            the value is read from config.
        include_ponytail: Forwarded to registered sections via ``PromptEnv``.
        extra_instructions: Optional extra instructions appended to (or
            replacing) the base identity block.
        extra_instructions_mode: ``"append"`` (default) inserts the extra
            block after the base identity; ``"replace"`` swaps the base
            identity out entirely.
        skills: Explicit skill list; defaults to ``context.skills``.
    """
    if context is None:
        context = Context.load(cwd)

    base = _resolve_base(base_content)
    if extra_instructions and extra_instructions_mode == "replace":
        base = extra_instructions
    sections: list[str] = [base] if base else []

    if extra_instructions and extra_instructions_mode == "append":
        sections.append(extra_instructions)

    env = PromptEnv(
        cwd=cwd,
        context=context,
        tools=tools,
        skills=skills,
        include_git_context=_resolve_git_flag(include_git_context),
        include_ponytail=_resolve_ponytail_flag(include_ponytail),
    )
    for section in prompt_sections():
        try:
            text = section.render(env)
        except Exception:
            text = None
        if text:
            sections.append(text)

    return "\n\n".join(sections)


__all__ = ["build_system_prompt"]
