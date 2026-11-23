"""``ctx.prompts`` — system-prompt section assembly."""

from __future__ import annotations

from pydantic import BaseModel

from ctx.plugins.plugin import plugin
from ctx.plugins.services import PromptService


class Config(BaseModel):
    """Whether the git snapshot and ponytail sections are assembled by default."""

    include_git_context: bool | None = None
    include_ponytail: bool | None = None


@plugin(name="prompts", config=Config)
def apply(ctx, config: Config):
    # The harness contributes no prompt sections of its own: the product
    # package registers its persona (base prompt), mode sections (e.g.
    # ponytail), and service-level extras (e.g. the todo list) through
    # ``ctx.agent.prompts.registry`` and ``ctx.prompts.add_section``.
    service = PromptService()
    ctx.provide("prompts", service)


PLUGIN = apply
