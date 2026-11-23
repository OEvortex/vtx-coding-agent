"""``ctx.prompts`` — system-prompt section assembly."""

from __future__ import annotations

from pydantic import BaseModel

from ctx.plugins.plugin import plugin
from ctx.plugins.services import PromptService


class Config(BaseModel):
    """Whether the git snapshot and ponytail sections are assembled by default."""

    include_git_context: bool | None = None
    include_ponytail: bool | None = None


def _render_todo_context(cwd: str, tools) -> str | None:
    """The live todo list, when ``llm.system_prompt.todo_context`` is on."""
    from ctx.core.config import config as ctx_config

    if not getattr(ctx_config.llm.system_prompt, "todo_context", False):
        return None
    from ctx.agent.prompts.todo import build_todo_section

    return build_todo_section() or None


@plugin(name="prompts", config=Config)
def apply(ctx, config: Config):
    service = PromptService()
    service.add_section("todo", _render_todo_context)
    ctx.provide("prompts", service)


PLUGIN = apply
