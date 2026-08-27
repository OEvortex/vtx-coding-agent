from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field, field_validator

from vtx.ai.agent.tools.base import BaseTool
from vtx.core import AskUserEvent, AskUserOption, AskUserQuestion, AskUserResponse
from vtx.core.types import ToolResult

MAX_QUESTIONS = 5
MIN_QUESTIONS = 1
MAX_OPTIONS = 5
MIN_OPTIONS = 2
MAX_HEADER_CHARS = 12
MAX_QUESTION_CHARS = 200
MAX_LABEL_CHARS = 60
MAX_DESCRIPTION_CHARS = 120
MAX_PREVIEW_CHARS = 240

RESERVED_OPTION_LABELS = {
    "other",
    "other...",
    "custom",
    "custom...",
    "type something else",
    "type something else...",
    "none of the above",
}


def validate_option_labels(
    options: list[AskUserOptionParam], question_idx: int | None = None
) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    prefix = f"Question {question_idx + 1}: " if question_idx is not None else ""

    for opt_idx, opt in enumerate(options):
        cleaned = opt.label.strip()
        lower = cleaned.lower()

        if lower in RESERVED_OPTION_LABELS:
            errors.append(
                f"{prefix}Option {opt_idx + 1} has reserved label {opt.label!r}. "
                "Do not include 'Other' options; the UI provides custom write-in automatically."
            )

        if lower in seen:
            errors.append(f"{prefix}Duplicate option label {opt.label!r}.")
        seen.add(lower)

    return errors


class AskUserOptionParam(BaseModel):
    label: str = Field(
        min_length=1,
        max_length=MAX_LABEL_CHARS,
        description=(
            "The selectable text displayed to the user (e.g. 'Use uv (Recommended)'). "
            "Keep concise."
        ),
    )
    description: str | None = Field(
        default=None,
        max_length=MAX_DESCRIPTION_CHARS,
        description="Optional brief subtitle explaining what this option means or its trade-offs.",
    )


class AskUserQuestionParam(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=MAX_QUESTION_CHARS,
        description="The question prompt displayed to the user. State clearly and concisely.",
    )
    header: str | None = Field(
        default=None,
        max_length=MAX_HEADER_CHARS,
        description=(
            "Optional short category tag shown as a chip/badge "
            "(e.g. 'Auth', 'Database', 'Scope'). Max 12 chars."
        ),
    )
    options: list[AskUserOptionParam] = Field(
        min_length=MIN_OPTIONS,
        max_length=MAX_OPTIONS,
        description=(
            f"Selectable choices ({MIN_OPTIONS}-{MAX_OPTIONS}). "
            "Do NOT include an 'Other' option; the UI provides a write-in field automatically."
        ),
    )
    multi_select: bool = Field(
        default=False,
        description=(
            "Set to true if multiple options can be chosen simultaneously with checkboxes."
        ),
    )

    @field_validator("options")
    @classmethod
    def check_options(cls, v: list[AskUserOptionParam]) -> list[AskUserOptionParam]:
        errors = validate_option_labels(v)
        if errors:
            raise ValueError("; ".join(errors))
        return v


class AskUserParams(BaseModel):
    questions: list[AskUserQuestionParam] = Field(
        min_length=MIN_QUESTIONS,
        max_length=MAX_QUESTIONS,
        description=(
            f"List of {MIN_QUESTIONS} to {MAX_QUESTIONS} structured questions "
            "to present to the user."
        ),
    )


class AskUserTool(BaseTool[AskUserParams]):
    name = "ask_user"
    params = AskUserParams
    tool_icon = "?"
    mutating = False

    description = (
        "Ask the user 1-5 structured questions with predefined options to clarify requirements, "
        "confirm architecture decisions, or resolve ambiguities before taking action. "
        "The user sees an interactive modal with clickable cards, multi-select checkboxes, "
        "and a freeform write-in option. Execution pauses until the user responds."
    )

    prompt_guidelines = (
        (
            "Use `ask_user` when requirements are genuinely ambiguous, high-impact "
            "architectural choices need user buy-in, or multiple valid approaches exist."
        ),
        (
            "Do NOT call `ask_user` for trivial clarifications that can be inferred from "
            "context, for routine next steps, or just to say hello/confirm you are starting."
        ),
        ("Do NOT add an 'Other' option — the UI always provides a write-in input by default."),
        (
            "Structure each question with 2-5 distinct, concrete choices. Put your "
            "recommended option first and prefix its label with '(Recommended)'."
        ),
    )

    def format_call(self, params: AskUserParams) -> str:
        n = len(params.questions)
        if n == 1:
            q = params.questions[0]
            header_prefix = f"[{q.header}] " if q.header else ""
            preview = f"{header_prefix}{q.question}"
            if len(preview) > MAX_PREVIEW_CHARS:
                preview = preview[: MAX_PREVIEW_CHARS - 3] + "..."
            return preview
        return f"{n} questions"

    async def execute(
        self,
        params: AskUserParams,
        cancel_event: asyncio.Event | None = None,
        tool_call_id: str | None = None,
    ) -> ToolResult:
        core_questions = [
            AskUserQuestion(
                question=_q.question,
                header=_q.header,
                options=[
                    AskUserOption(label=opt.label, description=opt.description)
                    for opt in _q.options
                ],
                multi_select=_q.multi_select,
            )
            for _q in params.questions
        ]

        loop = asyncio.get_running_loop()
        future: asyncio.Future[AskUserResponse] = loop.create_future()
        event = AskUserEvent(questions=core_questions, future=future, tool_call_id=tool_call_id)

        from vtx.ai.agent.loop import current_event_queue

        queue = current_event_queue.get()
        if queue is None:
            return ToolResult(
                success=False,
                result="ask_user tool executed outside of an active agent turn (no event queue).",
            )

        await queue.put(event)

        if cancel_event is not None:
            cancel_task = asyncio.create_task(cancel_event.wait())
            done, _ = await asyncio.wait(
                [asyncio.create_task(future), cancel_task], return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_task in done and not future.done():
                future.cancel()
                return ToolResult(success=False, result="User cancelled interaction.")
            cancel_task.cancel()

        response: AskUserResponse = await future

        if response.cancelled:
            return ToolResult(
                success=False,
                result="The user dismissed the question dialog without answering.",
                ui_summary="cancelled by user",
            )

        answers = response.answers
        lines: list[str] = []
        for i, q in enumerate(params.questions):
            ans = answers.get(i)
            header_prefix = f"[{q.header}] " if q.header else ""
            if ans is None:
                lines.append(f"{header_prefix}{q.question}\nAnswer: (skipped)")
            elif isinstance(ans, list):
                lines.append(f"{header_prefix}{q.question}\nAnswer: {', '.join(ans)}")
            else:
                lines.append(f"{header_prefix}{q.question}\nAnswer: {ans}")

        result_text = "\n\n".join(lines)

        summary_parts: list[str] = []
        for i, _q in enumerate(params.questions):
            ans = answers.get(i)
            if ans:
                val = ", ".join(ans) if isinstance(ans, list) else str(ans)
                if len(val) > 30:
                    val = val[:27] + "..."
                summary_parts.append(val)
        ui_summary = " · ".join(summary_parts) if summary_parts else "answered"

        return ToolResult(success=True, result=result_text, ui_summary=ui_summary)


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
