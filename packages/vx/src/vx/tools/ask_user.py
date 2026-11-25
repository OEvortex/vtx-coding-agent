"""The ``ask_user`` tool: stop mid-turn and ask, because guessing is worse.

The turn runner intercepts a call to this tool and hands the questions to the
UI (``ctx.agent.turn._run_ask_user``), so ``execute`` never runs in a real
session — it raises, so a regression is loud instead of silent.

The shape is deliberately small: 1-4 questions, each with 2-4 options or none
(free text). vx draws the questions as one transcript row, so everything the
model asks is part of the record of the turn that asked it.
"""

from __future__ import annotations

import asyncio

from ctx.agent.tools.base import BaseTool, ToolResult
from pydantic import BaseModel, Field, field_validator, model_validator

MIN_OPTIONS = 1
MAX_OPTIONS = 9

#: Names a model may use for the question's text instead of ``question``.
_QUESTION_ALIASES = ("text", "prompt", "title", "question_text", "q")
#: And for an option's label instead of ``label``.
_LABEL_ALIASES = ("text", "value", "title", "name", "option", "id")


def _usable_questions(questions: list) -> list:
    """Drop questions with nothing to say, and name the ones that only have choices.

    A model that runs out of wording sends ``{"question": "", "options": …}``.
    The choices carry the meaning, so that question is still askable; one with
    neither text nor choices is not, and is dropped rather than failing the call.
    """
    usable: list = []
    for question in questions:
        if not isinstance(question, dict):
            usable.append(question)
            continue
        data = dict(question)
        text = str(data.get("question") or "").strip() or _first_str(data, _QUESTION_ALIASES)
        choices = data.get("options") or data.get("choices") or data.get("answers")
        if not text:
            text = str(data.get("header") or "").strip()
            if not text:
                if not choices:
                    continue
                text = "Which one?"
        data["question"] = text
        usable.append(data)
    return usable


def _first_str(data: dict, names: tuple[str, ...]) -> str | None:
    for name in names:
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None


class AskUserOptionParam(BaseModel):
    label: str = Field(default="", description="Short label")
    description: str = Field(default="", description="Optional one-line explanation")
    preview: str | None = Field(
        default=None, description="Optional markdown shown under this option while it is selected"
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, value: object) -> object:
        """Take a bare string as a label, and a renamed key as one too.

        Models write ``options: ["main.py", "app.py"]`` far more often than the
        object form, and a validation error there costs a whole retry.
        """
        if isinstance(value, str):
            return {"label": value}
        if isinstance(value, dict) and not value.get("label"):
            label = _first_str(value, _LABEL_ALIASES)
            if label is not None:
                return {**value, "label": label}
        return value


class AskUserQuestionParam(BaseModel):
    question: str = Field(
        min_length=1, description="Short, specific question (put choices in options, not here)"
    )
    header: str | None = Field(default=None, description="Short noun tag for this question")
    options: list[AskUserOptionParam] | None = Field(
        default=None, description=f"{MIN_OPTIONS}-{MAX_OPTIONS} options; omit for free text"
    )
    multi_select: bool = Field(default=False, description="Allow multiple selections")

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, value: object) -> object:
        """Accept a bare question, an alias for its text, or plain-string options."""
        if isinstance(value, str):
            return {"question": value}
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not data.get("question"):
            question = _first_str(data, _QUESTION_ALIASES)
            if question is not None:
                data["question"] = question
        if "options" not in data:
            # ``choices`` is the other name models reach for.
            for alias in ("choices", "answers"):
                if alias in data:
                    data["options"] = data[alias]
                    break
        options = data.get("options")
        if isinstance(options, list) and not options:
            # An empty list is the free-text shape written the long way.
            data["options"] = None
        elif isinstance(options, str):
            # "main.py or app.py" is a sentence, not a schema: split it.
            parts = [part.strip(" .") for part in options.replace("/", ",").split(",")]
            data["options"] = [part for part in parts if part] or None
        elif isinstance(options, dict):
            data["options"] = [options]
        return data

    @field_validator("options")
    @classmethod
    def _validate_options(
        cls, value: list[AskUserOptionParam] | None
    ) -> list[AskUserOptionParam] | None:
        """Blank labels are dropped, and no choices left means free text."""
        if value is None:
            return None
        kept = [option for option in value if option.label.strip()]
        if not kept:
            return None
        if len(kept) > MAX_OPTIONS:
            # Digits stop at nine; the band still moves for the rest.
            raise ValueError(
                f"options must contain at most {MAX_OPTIONS} items (got {len(kept)})."
            )
        return kept


class AskUserParams(BaseModel):
    questions: list[AskUserQuestionParam] = Field(
        min_length=1,
        description=(
            "Questions asked together (one call, one row). Each carries "
            f"{MIN_OPTIONS}-{MAX_OPTIONS} options with a label and a one-line "
            "description, or no options for free text."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, value: object) -> object:
        """Fold the flat single-question shape into ``questions``.

        ``{question, options}`` is the shape most models reach for, and it is
        one question: wrapping it here beats rejecting the call and making the
        model say the whole thing again.
        """
        if isinstance(value, list):
            return {"questions": value}
        if isinstance(value, str):
            return {"questions": [value]}
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if isinstance(data.get("questions"), dict | str):
            data["questions"] = [data["questions"]]
        if isinstance(data.get("questions"), list):
            data["questions"] = _usable_questions(data["questions"])
        if not data.get("questions"):
            flat = {
                key: data[key]
                for key in (
                    "question",
                    "options",
                    "choices",
                    "answers",
                    "header",
                    "multi_select",
                    "text",
                    "prompt",
                )
                if key in data
            }
            if flat:
                data["questions"] = [flat]
        return data

    def normalized_questions(self) -> list[AskUserQuestionParam]:
        """The questionnaire as a plain list: the turn runner asks for this.

        vx has one shape (``questions``), so this only unwraps it, but the turn
        runner calls it for every ask_user call and the name is its contract.
        """
        return list(self.questions)


class AskUserTool(BaseTool):
    name = "ask_user"
    tool_icon = "?"
    params = AskUserParams
    mutating = False
    description = (
        "Ask the user a question and wait for the answer. 1-4 questions, each with "
        "2-4 options (label plus a one-line description) or none for free text. Use "
        "it when the next step depends on a choice only the user can make."
    )

    def format_call(self, params: AskUserParams) -> str:
        questions = params.questions
        if len(questions) == 1:
            question = questions[0]
            text = question.question.strip()
            prefix = f"[{question.header}] " if question.header else ""
            if question.options:
                return f"{prefix}{text}"
            return f"{prefix}{text} (free text)"
        return f"{len(questions)} questions"

    async def execute(
        self, params: AskUserParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        raise NotImplementedError(
            "ask_user is answered by the UI: the turn runner intercepts it and "
            "emits AskUserEvent, so execute() is never reached in a session."
        )


__all__ = ["AskUserOptionParam", "AskUserParams", "AskUserQuestionParam", "AskUserTool"]
