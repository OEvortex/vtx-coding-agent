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
    label: str = Field(
        min_length=1,
        max_length=MAX_LABEL_CHARS,
        description=(
            "Short label shown in the picker and returned to the LLM. "
            "Must be unique within the question."
        ),
    )
    description: str = Field(
        default="",
        max_length=MAX_DESCRIPTION_CHARS,
        description="Optional longer explanation shown under the label.",
    )


class AskUserParams(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=MAX_QUESTION_CHARS,
        description=(
            "The question to ask the user. Keep it short and specific. "
            "Avoid embedding options in the question — pass them via the "
            "``options`` field instead so the UI can render them as "
            "selectable choices."
        ),
    )
    options: list[AskUserOptionParam] | None = Field(
        default=None,
        description=(
            f"2-{MAX_OPTIONS} options to offer. Omit for an open-ended "
            "question that accepts free text only. The user can always "
            "type a custom answer via the synthetic 'Other' option."
        ),
    )
    multi_select: bool = Field(
        default=False, description="Allow the user to pick more than one option."
    )
    header: str | None = Field(
        default=None,
        max_length=MAX_HEADER_CHARS,
        description=(
            f"Optional short tag shown in the modal header (max "
            f"{MAX_HEADER_CHARS} chars). Use a noun, not a question "
            "(e.g. 'Package manager', 'Auth method')."
        ),
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
    prompt_guidelines = (
        "Use ask_user to ask a clarifying question before acting when the "
        "answer would change the approach, not for routine decisions. "
        "Prefer 2-4 options with a short label and one-line description; "
        "omit options for open-ended questions. Never use ask_user to ask "
        "questions the user can answer by running a tool (e.g. 'what files "
        "are in this dir?' — use find instead).",
    )
    description = (
        "Ask the user a clarifying question and wait for their answer. "
        "Pass 2-4 options for a multiple-choice question (use "
        "``multi_select`` to allow several), or omit options to accept "
        "free text. Returns the user's selection (labels) or the custom "
        "text they typed. The user can always type a custom answer even "
        "when options are given."
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
