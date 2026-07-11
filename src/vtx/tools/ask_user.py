"""The ``ask_user`` tool.

Lets the agent ask the user a clarifying question mid-turn. Two modes:

* **Multiple choice** — pass 2-4 ``options`` (each with a ``label`` and
  optional ``description``). The user picks one, or several if
  ``multi_select`` is true.
* **Open-ended** — omit ``options``. The user types free text.

A synthetic "Other" option is always appended so the user can answer
with custom text even when given a multiple-choice question.

The turn runner intercepts this tool (``turn.py:_run_ask_user``) and
yields an :class:`~vtx.events.AskUserEvent` rather than calling
``execute()``. ``execute()`` is kept for direct invocation and unit
tests; it raises so the intent is loud if interception regresses.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field, field_validator

from ..core.types import ToolResult
from .base import BaseTool

MIN_OPTIONS = 2
MAX_OPTIONS = 4
MAX_QUESTION_CHARS = 500
MAX_LABEL_CHARS = 80
MAX_DESCRIPTION_CHARS = 300
MAX_HEADER_CHARS = 12


class AskUserOptionParam(BaseModel):
    label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS, description="Short unique label")
    description: str = Field(
        default="", max_length=MAX_DESCRIPTION_CHARS, description="Optional one-line explanation"
    )


class AskUserParams(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=MAX_QUESTION_CHARS,
        description="Short, specific question (put choices in options, not here)",
    )
    options: list[AskUserOptionParam] | None = Field(
        default=None, description=f"2-{MAX_OPTIONS} options; omit for free text"
    )
    multi_select: bool = Field(default=False, description="Allow multiple selections")
    header: str | None = Field(
        default=None,
        max_length=MAX_HEADER_CHARS,
        description=f"Short noun tag for the modal (max {MAX_HEADER_CHARS} chars)",
    )

    @field_validator("options")
    @classmethod
    def _validate_options(
        cls, value: list[AskUserOptionParam] | None
    ) -> list[AskUserOptionParam] | None:
        if value is None:
            return None
        if len(value) < MIN_OPTIONS or len(value) > MAX_OPTIONS:
            raise ValueError(
                f"options must contain between {MIN_OPTIONS} and {MAX_OPTIONS} items "
                f"(got {len(value)})."
            )
        seen: set[str] = set()
        for opt in value:
            if opt.label in seen:
                raise ValueError(f"option labels must be unique (duplicate: {opt.label!r})")
            seen.add(opt.label)
        return value


class AskUserTool(BaseTool):
    name = "ask_user"
    tool_icon = "?"
    params = AskUserParams
    mutating = False
    prompt_guidelines = ()
    description = (
        "Ask the user a clarifying question and wait. Pass 2-4 options for "
        "multiple choice (multi_select for several), or omit for free text."
    )

    def format_call(self, params: AskUserParams) -> str:
        question = params.question.strip()
        prefix = f"[{params.header}] " if params.header else ""
        if params.options is not None:
            labels = [opt.label for opt in params.options]
            choices = " / ".join(labels) if len(labels) <= 2 else f"{len(labels)} options"
        else:
            choices = "free text"
        return f"{prefix}{question} ({choices})"

    async def execute(
        self, params: AskUserParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        # The turn runner intercepts ask_user before reaching execute().
        # If we got here, the tool was invoked outside the agent loop
        # (e.g. from a unit test or extension). Surface that loudly
        # instead of pretending the question was answered.
        raise NotImplementedError(
            "ask_user must be invoked through the turn runner so the user "
            "can be prompted. Use AskUserEvent directly when testing."
        )
