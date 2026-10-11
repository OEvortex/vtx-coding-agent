"""``ctx.llm`` — the provider-adapter seam."""

from __future__ import annotations

from pydantic import BaseModel

from vtx.plugins.plugin import plugin
from vtx.plugins.services import LLMService


class Config(BaseModel):
    """Deployment's provider default. ``None`` means "whatever the user picked"."""

    default_provider: str | None = None


@plugin(name="llm", config=Config)
def apply(ctx, config: Config):
    ctx.provide("llm", LLMService())


PLUGIN = apply
