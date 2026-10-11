"""``ctx.prompts`` — system-prompt section assembly."""

from __future__ import annotations

from pydantic import BaseModel

from vtx.plugins.plugin import plugin
from vtx.plugins.services import PromptService


class Config(BaseModel):
    """Whether the git snapshot and ponytail sections are assembled by default."""

    include_git_context: bool | None = None
    include_ponytail: bool | None = None


@plugin(name="prompts", config=Config)
def apply(ctx, config: Config):
    service = PromptService()
    ctx.provide("prompts", service)


PLUGIN = apply
