import asyncio

import pytest
from pydantic import ValidationError

from vtx.coding_agent.tools.ask_user import (
    MAX_DESCRIPTION_CHARS,
    MAX_HEADER_CHARS,
    MAX_LABEL_CHARS,
    MAX_QUESTION_CHARS,
    AskUserOptionParam,
    AskUserParams,
    AskUserQuestionParam,
    AskUserTool,
)
from vtx.core import AskUserAnswer, AskUserEvent, AskUserOption, AskUserQuestion, AskUserResponse


class TestAskUserParamsValidation:
    def test_minimal_params(self):
        # Just a question: open-ended
        p = AskUserParams(question="What's up?")
        assert p.question == "What's up?"
        assert p.options is None
        assert p.multi_select is False
        assert p.header is None

    def test_multiple_choice_params(self):
        p = AskUserParams(
            question="Pick a manager",
            options=[
                AskUserOptionParam(label="npm"),
                AskUserOptionParam(label="pnpm", description="faster"),
            ],
            multi_select=False,
            header="Pkg mgr",
        )
        assert p.options is not None
        assert len(p.options) == 2
        assert p.multi_select is False
        assert p.header == "Pkg mgr"

    def test_question_required(self):
        with pytest.raises(ValidationError):
            AskUserParams(question="")

    def test_question_max_length(self):
        AskUserParams(question="x" * MAX_QUESTION_CHARS)  # exact max: ok
        with pytest.raises(ValidationError):
            AskUserParams(question="x" * (MAX_QUESTION_CHARS + 1))

    def test_header_max_length(self):
        AskUserParams(question="x", header="y" * MAX_HEADER_CHARS)  # exact max: ok
        with pytest.raises(ValidationError):
            AskUserParams(question="x", header="y" * (MAX_HEADER_CHARS + 1))

    def test_label_max_length(self):
        AskUserOptionParam(label="x" * MAX_LABEL_CHARS)  # exact max: ok
        with pytest.raises(ValidationError):
            AskUserOptionParam(label="x" * (MAX_LABEL_CHARS + 1))

    def test_description_max_length(self):
        AskUserOptionParam(label="x", description="y" * MAX_DESCRIPTION_CHARS)  # ok
        with pytest.raises(ValidationError):
            AskUserOptionParam(label="x", description="y" * (MAX_DESCRIPTION_CHARS + 1))

    def test_one_option_rejected(self):
        with pytest.raises(ValidationError):
            AskUserParams(question="x", options=[AskUserOptionParam(label="only")])

    def test_five_options_rejected(self):
        with pytest.raises(ValidationError):
            AskUserParams(
                question="x", options=[AskUserOptionParam(label=f"o{i}") for i in range(5)]
            )

    def test_duplicate_labels_rejected(self):
        with pytest.raises(ValidationError, match="unique"):
            AskUserParams(
                question="x",
                options=[AskUserOptionParam(label="same"), AskUserOptionParam(label="same")],
            )

    def test_two_options_allowed(self):
        # Lower bound on multiple-choice: 2
        p = AskUserParams(
            question="x", options=[AskUserOptionParam(label="a"), AskUserOptionParam(label="b")]
        )
        assert p.options is not None and len(p.options) == 2

    def test_four_options_allowed(self):
        # Upper bound: 4
        p = AskUserParams(
            question="x", options=[AskUserOptionParam(label=f"o{i}") for i in range(4)]
        )
        assert p.options is not None and len(p.options) == 4


class TestAskUserToolFormatting:
    def test_format_call_open_ended(self):
        t = AskUserTool()
        p = AskUserParams(question="What is your favorite color?")
        call = t.format_call(p)
        assert "What is your favorite color?" in call
        assert "free text" in call
        assert "[" not in call  # no header

    def test_format_call_two_options(self):
        t = AskUserTool()
        p = AskUserParams(
            question="Pick a package manager",
            options=[AskUserOptionParam(label="npm"), AskUserOptionParam(label="pnpm")],
        )
        call = t.format_call(p)
        assert "Pick a package manager" in call
        assert "npm" in call and "pnpm" in call
        assert "/" in call  # joined by /

    def test_format_call_three_or_more_options(self):
        t = AskUserTool()
        p = AskUserParams(
            question="Pick a tool", options=[AskUserOptionParam(label=f"t{i}") for i in range(3)]
        )
        call = t.format_call(p)
        assert "3 options" in call

    def test_format_call_with_header(self):
        t = AskUserTool()
        p = AskUserParams(
            question="Pick a manager",
            options=[AskUserOptionParam(label="a"), AskUserOptionParam(label="b")],
            header="Pkg",
        )
        call = t.format_call(p)
        assert call.startswith("[Pkg] ")

    def test_execute_raises_outside_turn_runner(self):
        t = AskUserTool()
        p = AskUserParams(question="x")
        with pytest.raises(NotImplementedError, match="turn runner"):
            asyncio.run(t.execute(p))


class TestAskUserResponse:
    def test_empty_response(self):
        r = AskUserResponse()
        assert r.is_empty is True

    def test_empty_string_custom_text(self):
        r = AskUserResponse(custom_text="   ")
        assert r.is_empty is True

    def test_selections_response(self):
        r = AskUserResponse(selections=("a",))
        assert r.is_empty is False

    def test_custom_text_response(self):
        r = AskUserResponse(custom_text="hello")
        assert r.is_empty is False

    def test_format_for_llm_selections(self):
        r = AskUserResponse(selections=("npm", "pnpm"))
        text = r.format_for_llm([])
        assert "npm" in text and "pnpm" in text
        assert "selected" in text.lower()

    def test_format_for_llm_custom_text(self):
        r = AskUserResponse(custom_text="my answer")
        text = r.format_for_llm([])
        assert "my answer" in text
        assert "custom" in text.lower()

    def test_format_for_llm_empty(self):
        r = AskUserResponse()
        text = r.format_for_llm([])
        assert "declined" in text.lower()

    def test_ui_summary_selections(self):
        r = AskUserResponse(selections=("a", "b"))
        s = r.ui_summary()
        assert "a" in s and "b" in s

    def test_ui_summary_custom_text_truncates(self):
        r = AskUserResponse(custom_text="x" * 100)
        s = r.ui_summary()
        # Long text should be truncated with ellipsis
        assert "..." in s

    def test_ui_summary_empty(self):
        r = AskUserResponse()
        s = r.ui_summary()
        assert "no answer" in s


class TestAskUserEvent:
    def test_default_construction(self):
        ev = AskUserEvent()
        assert ev.questions == []
        assert ev.future is None

    def test_with_questions(self):
        opts = [AskUserOption(label="a"), AskUserOption(label="b")]
        q = AskUserQuestion(question="q", options=opts, multi_select=True)
        ev = AskUserEvent(questions=[q])
        assert ev.questions == [q]
        assert ev.questions[0].multi_select is True


class TestQuestionnaireParams:
    def _opts(self, n: int = 2) -> list[AskUserOptionParam]:
        return [AskUserOptionParam(label=f"o{i}") for i in range(n)]

    def test_single_question_via_questions(self):
        p = AskUserParams(
            questions=[AskUserQuestionParam(question="Pick one", options=self._opts())]
        )
        normalized = p.normalized_questions()
        assert len(normalized) == 1
        assert normalized[0].question == "Pick one"

    def test_four_questions_allowed(self):
        qs = [AskUserQuestionParam(question=f"q{i}?", options=self._opts()) for i in range(4)]
        p = AskUserParams(questions=qs)
        assert len(p.normalized_questions()) == 4

    def test_five_questions_rejected(self):
        qs = [AskUserQuestionParam(question=f"q{i}?", options=self._opts()) for i in range(5)]
        with pytest.raises(ValidationError, match="between 1 and 4"):
            AskUserParams(questions=qs)

    def test_empty_questions_rejected(self):
        with pytest.raises(ValidationError, match="between 1 and 4"):
            AskUserParams(questions=[])

    def test_duplicate_question_text_rejected(self):
        with pytest.raises(ValidationError, match="unique"):
            AskUserParams(
                questions=[
                    AskUserQuestionParam(question="same?", options=self._opts()),
                    AskUserQuestionParam(question="same?", options=self._opts()),
                ]
            )

    def test_reserved_labels_rejected(self):
        for reserved in ("Other", "Type something.", "Next"):
            with pytest.raises(ValidationError, match="reserved"):
                AskUserParams(
                    question="x",
                    options=[AskUserOptionParam(label="a"), AskUserOptionParam(label=reserved)],
                )

    def test_nested_reserved_labels_rejected(self):
        with pytest.raises(ValidationError, match="reserved"):
            AskUserParams(
                questions=[
                    AskUserQuestionParam(
                        question="x",
                        options=[AskUserOptionParam(label="a"), AskUserOptionParam(label="Next")],
                    )
                ]
            )

    def test_preview_on_multi_select_rejected(self):
        with pytest.raises(ValidationError, match="preview"):
            AskUserParams(
                questions=[
                    AskUserQuestionParam(
                        question="x",
                        multi_select=True,
                        options=[
                            AskUserOptionParam(label="a", preview="# hi"),
                            AskUserOptionParam(label="b"),
                        ],
                    )
                ]
            )

    def test_preview_on_single_select_allowed(self):
        p = AskUserParams(
            questions=[
                AskUserQuestionParam(
                    question="x",
                    options=[
                        AskUserOptionParam(label="a", preview="```py\nprint(1)\n```"),
                        AskUserOptionParam(label="b"),
                    ],
                )
            ]
        )
        assert p.normalized_questions()[0].options is not None

    def test_normalized_wraps_legacy_fields(self):
        p = AskUserParams(
            question="legacy?",
            header="H",
            options=[AskUserOptionParam(label="a"), AskUserOptionParam(label="b")],
            multi_select=True,
        )
        normalized = p.normalized_questions()
        assert len(normalized) == 1
        assert normalized[0].question == "legacy?"
        assert normalized[0].header == "H"
        assert normalized[0].multi_select is True
        assert normalized[0].options is not None and len(normalized[0].options) == 2


class TestEnvelopeFormat:
    def _answer(self, **kwargs) -> AskUserAnswer:
        defaults = {"question": "Build tool?", "kind": "option"}
        defaults.update(kwargs)
        return AskUserAnswer(**defaults)

    def test_option_answer_envelope(self):
        r = AskUserResponse(answers=(self._answer(answer="uv"),))
        text = r.format_for_llm()
        assert text.startswith("User has answered your questions:")
        assert '"Build tool?"="uv"' in text
        assert text.endswith("You can now continue with the user's answers in mind.")

    def test_multi_answer_joins_selections(self):
        r = AskUserResponse(
            answers=(self._answer(kind="multi", selected=("unit", "integration")),)
        )
        assert '"Build tool?"="unit, integration"' in r.format_for_llm()

    def test_custom_answer_envelope(self):
        r = AskUserResponse(answers=(self._answer(kind="custom", answer="use make"),))
        assert '"Build tool?"="use make"' in r.format_for_llm()

    def test_decline_message_when_empty(self):
        assert AskUserResponse().format_for_llm() == "User declined to answer questions"

    def test_preview_segment(self):
        r = AskUserResponse(answers=(self._answer(answer="uv", preview="```toml\n[tool]\n```"),))
        assert "selected preview:" in r.format_for_llm()

    def test_ui_summary_compacts_answers(self):
        r = AskUserResponse(
            answers=(
                self._answer(question="Q1", answer="alpha"),
                self._answer(question="Q2", kind="multi", selected=("b", "c")),
            )
        )
        summary = r.ui_summary()
        assert "alpha" in summary
        assert "b, c" in summary
