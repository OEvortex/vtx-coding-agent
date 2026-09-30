"""Back-compat alias for :mod:`vtx.ai.agent.prompts.rlm`."""

from __future__ import annotations

from vtx.ai.agent.prompts.rlm import (
    DEFAULT_RLM_EXTRA_IMPORT_LABELS,
    build_child_agent_doctrine,
    build_rlm_system_prompt,
    build_subagent_guidance,
)

__all__ = [
    "DEFAULT_RLM_EXTRA_IMPORT_LABELS",
    "build_child_agent_doctrine",
    "build_rlm_system_prompt",
    "build_subagent_guidance",
]
