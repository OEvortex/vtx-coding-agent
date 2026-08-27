"""The ``ask_user`` tool.

Re-exports the harness-native ``ask_user`` tool from :mod:`vtx.ai.agent.tools.ask_user`.
"""

from __future__ import annotations

from vtx.ai.agent.tools.ask_user import (
    MAX_DESCRIPTION_CHARS,
    MAX_HEADER_CHARS,
    MAX_LABEL_CHARS,
    MAX_OPTIONS,
    MAX_PREVIEW_CHARS,
    MAX_QUESTION_CHARS,
    MAX_QUESTIONS,
    MIN_OPTIONS,
    MIN_QUESTIONS,
    RESERVED_OPTION_LABELS,
    AskUserOptionParam,
    AskUserParams,
    AskUserQuestionParam,
    AskUserTool,
    validate_option_labels,
)

__all__ = [
    "MAX_DESCRIPTION_CHARS",
    "MAX_HEADER_CHARS",
    "MAX_LABEL_CHARS",
    "MAX_OPTIONS",
    "MAX_PREVIEW_CHARS",
    "MAX_QUESTIONS",
    "MAX_QUESTION_CHARS",
    "MIN_OPTIONS",
    "MIN_QUESTIONS",
    "RESERVED_OPTION_LABELS",
    "AskUserOptionParam",
    "AskUserParams",
    "AskUserQuestionParam",
    "AskUserTool",
    "validate_option_labels",
]
