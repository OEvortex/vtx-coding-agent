"""The ``task`` subagent tool.

Re-exports the harness-native ``task`` tool from :mod:`vtx.ai.agent.tools.task`.
"""

from __future__ import annotations

from vtx.ai.agent.tools.task import (
    MAX_RESULT_CHARS,
    MAX_TRANSCRIPT_LINES,
    SUBAGENT_FINAL_ANSWER_DIRECTIVE,
    SubagentRunResult,
    SubagentSpec,
    TaskParams,
    TaskTool,
    _build_subagent_system_prompt,
    _build_subagent_tool_list,
    _create_subagent_session,
    _format_tokens,
    _format_transcript,
    _resolve_api_and_base_url,
    _resolve_subagent_spec,
    _run_subagent,
    _spec_from_preset,
)

__all__ = [
    "MAX_RESULT_CHARS",
    "MAX_TRANSCRIPT_LINES",
    "SUBAGENT_FINAL_ANSWER_DIRECTIVE",
    "SubagentRunResult",
    "SubagentSpec",
    "TaskParams",
    "TaskTool",
    "_build_subagent_system_prompt",
    "_build_subagent_tool_list",
    "_create_subagent_session",
    "_format_tokens",
    "_format_transcript",
    "_resolve_api_and_base_url",
    "_resolve_subagent_spec",
    "_run_subagent",
    "_spec_from_preset",
]
