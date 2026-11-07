"""``ctx.prompts`` — system-prompt section assembly."""

from __future__ import annotations

from pydantic import BaseModel

from vtx.plugins.plugin import plugin
from vtx.plugins.services import PromptService


class Config(BaseModel):
    """Whether the git snapshot and ponytail sections are assembled by default."""

    include_git_context: bool | None = None
    include_ponytail: bool | None = None


def _render_todo_context(cwd: str, tools) -> str | None:
    """The live todo list, when ``llm.system_prompt.todo_context`` is on."""
    from vtx.core.config import config as vtx_config

    if not getattr(vtx_config.llm.system_prompt, "todo_context", False):
        return None
    from vtx.agent.prompts.todo import build_todo_section

    return build_todo_section() or None


@plugin(name="prompts", config=Config)
def apply(ctx, config: Config):
    service = PromptService()
    service.add_section("todo", _render_todo_context)
    ctx.provide("prompts", service)


PLUGIN = apply
