"""Back-compat alias for :mod:`vtx.ai.agent.prompts.ponytail`."""

from __future__ import annotations

from vtx.ai.agent.prompts.ponytail import (
    PONYTAIL_PROMPT,
    build_ponytail_section,
    is_deactivation_command,
    register,
)

__all__ = ["PONYTAIL_PROMPT", "build_ponytail_section", "is_deactivation_command", "register"]
