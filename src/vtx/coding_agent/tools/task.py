"""The ``task`` subagent tool.

Re-exports the harness-native ``task`` tool from :mod:`vtx.ai.agent.tools.task`.
"""

from __future__ import annotations

from vtx.ai.agent.tools.task import (
    MAX_RESULT_CHARS,
    MAX_TRANSCRIPT_LINES,
    SubagentSpec,
    TaskParams,
    TaskTool,
    _format_tokens,
    _format_transcript,
    _resolve_subagent_spec,
    _run_subagent,
)

__all__ = [
    "MAX_RESULT_CHARS",
    "MAX_TRANSCRIPT_LINES",
    "SubagentSpec",
    "TaskParams",
    "TaskTool",
    "_format_tokens",
    "_format_transcript",
    "_resolve_subagent_spec",
    "_run_subagent",
]
