import asyncio

import pytest
from pydantic import ValidationError

from vtx.events import AskUserEvent
from vtx.permissions import AskUserOption, AskUserResponse
from vtx.tools.ask_user import (
    MAX_DESCRIPTION_CHARS,
    MAX_HEADER_CHARS,
    MAX_LABEL_CHARS,
    MAX_QUESTION_CHARS,
    AskUserOptionParam,
    AskUserParams,
    AskUserTool,
)


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
        assert "did not" in text.lower()

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
        assert ev.question == ""
        assert ev.options == []
        assert ev.multi_select is False
        assert ev.future is None

    def test_with_options(self):
        opts = [AskUserOption(label="a"), AskUserOption(label="b")]
        ev = AskUserEvent(question="q", options=opts, multi_select=True)
        assert ev.question == "q"
        assert ev.options == opts
        assert ev.multi_select is True
