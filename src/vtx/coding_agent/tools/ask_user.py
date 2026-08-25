"""The ``ask_user`` tool.

Lets the agent ask the user clarifying questions mid-turn as an
rpiv-style questionnaire:

* **1-4 questions** arrive in a single tabbed dialog (``questions``),
  each with a short ``header`` chip for the tab strip.
* **Multiple choice** — each question carries 2-4 ``options`` (each
  with a ``label``, a one-line ``description``, and an optional
  markdown ``preview``). The user picks one, or several when
  ``multi_select`` is true.
* **Open-ended** — omit ``options`` and the user types free text.

A "Type something." row is appended to every question so the user can
always answer in their own words, even for multiple-choice questions.

The turn runner intercepts this tool (``turn.py:_run_ask_user``) and
yields an :class:`~vtx.events.AskUserEvent` rather than calling
``execute()``. ``execute()`` is kept for direct invocation and unit
tests; it raises so the intent is loud if interception regresses.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field, field_validator, model_validator

from vtx.ai.agent.tools.base import BaseTool
from vtx.core.types import ToolResult

MIN_OPTIONS = 2
MAX_OPTIONS = 4
MAX_QUESTIONS = 4
MIN_QUESTIONS = 1
MAX_QUESTION_CHARS = 500
MAX_LABEL_CHARS = 80
MAX_DESCRIPTION_CHARS = 300
MAX_PREVIEW_CHARS = 4000
MAX_HEADER_CHARS = 16

# Labels the dialog appends itself (or wants to keep unambiguous); the
# model may not author options that collide with them.
RESERVED_OPTION_LABELS = ("Other", "Type something.", "Next")


class AskUserOptionParam(BaseModel):
    label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS, description="Short unique label")
    description: str = Field(
        default="", max_length=MAX_DESCRIPTION_CHARS, description="Optional one-line explanation"
    )
    preview: str | None = Field(
        default=None,
        max_length=MAX_PREVIEW_CHARS,
        description="Optional markdown artifact (code, mockup, config) shown beside this option",
    )


def validate_option_labels(options: list[AskUserOptionParam]) -> None:
    """Reject reserved or duplicate labels; raises ``ValueError``."""
    seen: set[str] = set()
    for opt in options:
        if opt.label in RESERVED_OPTION_LABELS:
            raise ValueError(
                f"option label {opt.label!r} is reserved by the dialog UI; pick another"
            )
        if opt.label in seen:
            raise ValueError(f"option labels must be unique (duplicate: {opt.label!r})")
        seen.add(opt.label)


class AskUserQuestionParam(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=MAX_QUESTION_CHARS,
        description="Short, specific question (put choices in options, not here)",
    )
    header: str | None = Field(
        default=None,
        max_length=MAX_HEADER_CHARS,
        description=f"Short noun tag for the question's tab (max {MAX_HEADER_CHARS} chars)",
    )
    options: list[AskUserOptionParam] | None = Field(
        default=None, description=f"2-{MAX_OPTIONS} options; omit for free text"
    )
    multi_select: bool = Field(default=False, description="Allow multiple selections")

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
        validate_option_labels(value)
        return value

    @model_validator(mode="after")
    def _validate_previews(self) -> AskUserQuestionParam:
        # Multi-select tabs render checkbox rows; previews only fit the
        # single-select layout.
        if self.multi_select and self.options and any(opt.preview for opt in self.options):
            raise ValueError("preview is only supported on single-select questions")
        return self


class AskUserParams(BaseModel):
    question: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_QUESTION_CHARS,
        description="Short, specific single question (legacy shape; prefer `questions`)",
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
    questions: list[AskUserQuestionParam] | None = Field(
        default=None,
        description=(
            f"1-{MAX_QUESTIONS} questions asked together in one tabbed dialog; "
            "use instead of the single-question fields when asking several things at once"
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
        validate_option_labels(value)
        return value

    @field_validator("questions")
    @classmethod
    def _validate_questions(
        cls, value: list[AskUserQuestionParam] | None
    ) -> list[AskUserQuestionParam] | None:
        if value is None:
            return None
        if len(value) < MIN_QUESTIONS or len(value) > MAX_QUESTIONS:
            raise ValueError(
                f"questions must contain between {MIN_QUESTIONS} and {MAX_QUESTIONS} "
                f"items (got {len(value)})."
            )
        texts = [q.question.strip() for q in value]
        if len(set(texts)) != len(texts):
            raise ValueError("questions must be unique (two questions have identical text)")
        return value

    def normalized_questions(self) -> list[AskUserQuestionParam]:
        """Return the questionnaire as a list, wrapping legacy fields.

        The flat single-question fields (``question``/``options``/...)
        remain supported; they normalize into a one-entry list so the
        rest of the pipeline only ever deals with ``questions``.
        """
        if self.questions:
            return list(self.questions)
        return [
            AskUserQuestionParam(
                question=self.question or "",
                header=self.header,
                options=list(self.options) if self.options else None,
                multi_select=self.multi_select,
            )
        ]


class AskUserTool(BaseTool):
    name = "ask_user"
    tool_icon = "?"
    params = AskUserParams
    mutating = False
    prompt_guidelines = ()
    description = (
        "Ask the user clarifying questions and wait. Pass a `questions` array of "
        f"1-{MAX_QUESTIONS} questions, each with 2-4 written-out `options` (label plus a "
        "one-line description of what that choice means or costs), an optional "
        "`multi_select`, and an optional per-option markdown `preview`. The user can "
        "always answer in their own words instead of picking an option, and can attach "
        "notes. Ask whenever the next step depends on a real decision - one batched "
        "dialog beats five round-trips."
    )

    def format_call(self, params: AskUserParams) -> str:
        questions = params.normalized_questions()
        if len(questions) == 1:
            q = questions[0]
            question = q.question.strip()
            prefix = f"[{q.header}] " if q.header else ""
            if q.options is not None:
                labels = [opt.label for opt in q.options]
                choices = " / ".join(labels) if len(labels) <= 2 else f"{len(labels)} options"
            else:
                choices = "free text"
            return f"{prefix}{question} ({choices})"
        parts = [f"[{q.header or '?'}]" for q in questions]
        return f"{len(questions)} questions ({' '.join(parts)})"

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
