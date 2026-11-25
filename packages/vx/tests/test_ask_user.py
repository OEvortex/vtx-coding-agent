"""``ask_user``: the tool, its transcript row, and the keys that answer it."""

import asyncio
from dataclasses import dataclass
from typing import cast

import pytest
from ctx.core import AskUserEvent, AskUserOption, AskUserQuestion, AskUserResponse
from vx.app import VxApp
from vx.tools.ask_user import AskUserParams, AskUserTool
from vx.tui import ansi, blocks
from vx.tui.terminal import Terminal


@dataclass
class StubBlock(blocks.Block):
    kind: str = "assistant"
    text: str = "x"

    def rows(self, width: int) -> list[str]:
        return [self.text]


class StubTerminal:
    _fd = -1

    def size(self) -> tuple[int, int]:
        return 24, 80

    def write(self, data: str) -> None:
        pass

    def enter(self) -> None:
        pass

    def exit(self) -> None:
        pass


def make_app() -> VxApp:
    app = VxApp(cwd="/tmp", terminal=cast(Terminal, StubTerminal()))
    app._ready = asyncio.Event()
    app._ready.set()
    app._draw = lambda *args, **kwargs: None
    return app


def ask_current(app: VxApp) -> blocks.AskItem:
    """The open question of the pending call."""
    current = ask_block(app).current()
    assert current is not None
    return current


def ask_block(app: VxApp) -> blocks.AskUser:
    """The row the pending call drew, asserting there is one."""
    ask = app._ask
    assert ask is not None
    block = ask["block"]
    assert isinstance(block, blocks.AskUser)
    return block


def labels(question: object) -> list[str]:
    """The option labels of a parsed question, asserting it has options."""
    options = getattr(question, "options", None)
    assert options is not None
    return [option.label for option in options]


def ask_event(*questions: AskUserQuestion) -> tuple[AskUserEvent, asyncio.Future]:
    """An ``AskUserEvent`` with a future the test can inspect, as the turn makes it."""
    loop = asyncio.new_event_loop()
    future: asyncio.Future = loop.create_future()
    return AskUserEvent(tool_call_id="call_1", questions=list(questions), future=future), future


def one_question() -> AskUserQuestion:
    return AskUserQuestion(
        question="Which scrollback size should the transcript keep?",
        options=[
            AskUserOption(label="Keep everything", description="no limit"),
            AskUserOption(label="Keep 2,000 rows", description="a fold marker says what went"),
        ],
    )


def two_questions() -> tuple[AskUserQuestion, AskUserQuestion]:
    second = AskUserQuestion(
        question="Do subagents share the todo sidebar?",
        options=[AskUserOption(label="Yes"), AskUserOption(label="No")],
    )
    return one_question(), second


# -- the tool ---------------------------------------------------------


def test_the_flat_single_question_shape_is_folded_in():
    """`{question, options}` is what most models reach for: it is one question."""
    params = AskUserParams.model_validate(
        {"question": "which?", "options": [{"label": "a"}, {"label": "b"}]}
    )
    questions = params.normalized_questions()
    assert [question.question for question in questions] == ["which?"]
    assert labels(questions[0]) == ["a", "b"]


def test_options_may_be_plain_strings():
    params = AskUserParams.model_validate(
        {"questions": [{"question": "which?", "options": ["a", "b"]}]}
    )
    assert labels(params.questions[0]) == ["a", "b"]


def test_renamed_keys_are_read_as_question_and_label():
    params = AskUserParams.model_validate(
        {"questions": [{"prompt": "which?", "options": [{"text": "a"}, {"value": "b"}]}]}
    )
    assert params.questions[0].question == "which?"
    assert labels(params.questions[0]) == ["a", "b"]


def test_a_bare_question_is_a_free_text_question():
    params = AskUserParams.model_validate({"questions": ["what should it be called?"]})
    assert params.questions[0].options is None


def test_questions_may_arrive_as_one_object_or_a_top_level_list():
    single = AskUserParams.model_validate({"questions": {"question": "q", "options": ["a", "b"]}})
    listed = AskUserParams.model_validate([{"question": "q", "options": ["a", "b"]}])
    assert len(single.questions) == len(listed.questions) == 1


def test_an_option_sentence_is_split_into_choices():
    params = AskUserParams.model_validate({"question": "which?", "options": "main.py, app.py"})
    assert labels(params.questions[0]) == ["main.py", "app.py"]


def test_one_option_and_many_options_are_both_accepted():
    one = AskUserParams.model_validate({"questions": [{"question": "q", "options": ["only"]}]})
    nine = AskUserParams.model_validate(
        {"questions": [{"question": "q", "options": [str(index) for index in range(9)]}]}
    )
    assert len(labels(one.questions[0])) == 1
    assert len(labels(nine.questions[0])) == 9


def test_long_text_is_not_a_reason_to_reject_the_call():
    params = AskUserParams.model_validate(
        {
            "questions": [
                {
                    "header": "a-header-far-longer-than-the-tag-column",
                    "question": "why " * 200,
                    "options": ["x" * 200, "y" * 200],
                }
            ]
        }
    )
    assert params.questions[0].question.startswith("why why")


def test_empty_options_mean_free_text():
    params = AskUserParams.model_validate({"question": "what should it be called?", "options": []})
    assert params.questions[0].options is None


def test_choices_is_read_as_options():
    params = AskUserParams.model_validate({"question": "which?", "choices": ["a", "b"]})
    assert labels(params.questions[0]) == ["a", "b"]


def test_a_blank_question_with_choices_is_still_askable():
    """A model that runs out of wording sends `{question: "", options: [...]}`."""
    params = AskUserParams.model_validate(
        {
            "questions": [
                {"question": "what to include", "multi_select": True},
                {"question": "", "options": [{"label": "docs"}, {"label": "tests"}]},
            ]
        }
    )
    assert [question.question for question in params.questions] == [
        "what to include",
        "Which one?",
    ]
    assert labels(params.questions[1]) == ["docs", "tests"]


def test_a_question_with_nothing_to_say_is_dropped_not_fatal():
    params = AskUserParams.model_validate(
        {"questions": [{"question": ""}, {"question": "the real one"}]}
    )
    assert [question.question for question in params.questions] == ["the real one"]


def test_a_header_can_stand_in_for_a_missing_question():
    params = AskUserParams.model_validate(
        {"questions": [{"header": "Naming", "options": ["a", "b"]}]}
    )
    assert params.questions[0].question == "Naming"


def test_blank_option_labels_are_dropped():
    params = AskUserParams.model_validate(
        {"questions": [{"question": "q", "options": [{"label": ""}, {"label": "ok"}]}]}
    )
    assert labels(params.questions[0]) == ["ok"]


def test_a_call_with_no_questions_at_all_is_still_an_error():
    with pytest.raises(ValueError):
        AskUserParams.model_validate({})


def test_format_call_names_the_question_and_the_count():
    tool = AskUserTool()
    single = AskUserParams(
        questions=[{"question": "Pick one", "options": [{"label": "A"}, {"label": "B"}]}]
    )
    many = AskUserParams(questions=[{"question": "a"}, {"question": "b"}, {"question": "c"}])
    assert tool.format_call(single) == "Pick one"
    assert tool.format_call(many) == "3 questions"


def test_execute_is_never_reached():
    tool = AskUserTool()
    params = AskUserParams(questions=[{"question": "q"}])
    with pytest.raises(NotImplementedError):
        asyncio.run(tool.execute(params))


def test_the_tool_is_registered():
    from vx.tools import DEFAULT_TOOLS, get_tools

    assert "ask_user" in DEFAULT_TOOLS
    assert [tool.name for tool in get_tools(["ask_user"])] == ["ask_user"]


# -- the transcript row -----------------------------------------------


def test_waiting_row_shows_the_question_its_options_and_the_hint():
    block = blocks.AskUser(items=[blocks.AskItem(question="q", options=[blocks.AskChoice("a")])])
    rows = [ansi.strip_ansi(row) for row in block.rows(80)]
    assert rows[0].startswith("● Asked you a question")
    assert "waiting" in rows[0]
    assert rows[1].startswith("├ q")
    assert rows[-1].endswith("esc skips")


def test_answered_question_folds_to_one_line():
    block = blocks.AskUser(
        items=[
            blocks.AskItem(question="which size?", options=[blocks.AskChoice("a")], answer="a"),
            blocks.AskItem(
                question="next?", options=[blocks.AskChoice("b"), blocks.AskChoice("c")]
            ),
        ]
    )
    block.at = 1
    rows = [ansi.strip_ansi(row) for row in block.rows(80)]
    assert rows[0].endswith("2/2")
    assert "✓ which size?" in rows[1] and "→" in rows[1]
    assert any("next?" in row and row.startswith("├ next") for row in rows)


def test_a_pending_question_is_one_dim_line():
    block = blocks.AskUser(
        items=[blocks.AskItem(question="first"), blocks.AskItem(question="second")]
    )
    rows = [ansi.strip_ansi(row) for row in block.rows(80)]
    assert any("├ · second" in row for row in rows)


def test_a_multi_select_answered_by_typing_shows_the_text():
    block = blocks.AskUser(
        items=[
            blocks.AskItem(
                question="what to include",
                options=[blocks.AskChoice("docs"), blocks.AskChoice("tests")],
                multi=True,
                answer="docs and tests",
                custom=True,
            )
        ]
    )
    block.at = 1
    rows = [ansi.strip_ansi(row) for row in block.rows(80)]
    assert any("→  docs and tests" in row for row in rows)


def test_skip_leaves_an_honest_partial_result():
    block = blocks.AskUser(
        items=[blocks.AskItem(question="one", answer="a"), blocks.AskItem(question="two")]
    )
    block.at = 2
    block.skipped = True
    rows = [ansi.strip_ansi(row) for row in block.rows(80)]
    assert "✗" in rows[0]
    assert rows[-1] == "└ 1 of 2 answered · skipped 1"


def test_the_typed_answer_is_visible():
    block = blocks.AskUser(items=[blocks.AskItem(question="q")], draft="just the pinned ones")
    rows = [ansi.strip_ansi(row) for row in block.rows(80)]
    assert any("just the pinned ones" in row for row in rows)


@pytest.mark.parametrize("width", [12, 24, 40, 60, 88, 120, 200])
def test_no_row_is_wider_than_the_width(width):
    block = blocks.AskUser(
        items=[
            blocks.AskItem(
                question="A question long enough to need wrapping at every narrow width?",
                options=[
                    blocks.AskChoice("Keep everything", "no limit, rows only leave when deleted"),
                    blocks.AskChoice("Keep 2,000 rows", "a fold marker says what was dropped"),
                ],
                answer="Keep everything",
            ),
            blocks.AskItem(question="Still to come?"),
        ]
    )
    for state in (0, 1, 2):
        block.at = state
        for row in block.rows(width):
            assert ansi.width(row) <= width


def test_a_multi_select_question_shows_checkboxes():
    block = blocks.AskUser(
        items=[
            blocks.AskItem(
                question="What to include?",
                options=[blocks.AskChoice("docs"), blocks.AskChoice("tests")],
                multi=True,
                checked=[0],
            )
        ]
    )
    rows = [ansi.strip_ansi(row) for row in block.rows(80)]
    assert any("[x] docs" in row for row in rows)
    assert any("[ ] tests" in row for row in rows)
    assert "toggles" in rows[-1]


# -- answering --------------------------------------------------------


def test_the_call_starts_as_a_transcript_row_and_blocks():
    app = make_app()
    event, future = ask_event(one_question())
    app._request_ask_user(event)
    assert app._ask is not None
    assert not future.done()
    block = ask_block(app)
    assert app.viewport.blocks[-1] is block


def test_a_digit_answers_and_resolves_the_future():
    app = make_app()
    event, future = ask_event(one_question())
    app._request_ask_user(event)
    app._handle_key("2")
    assert future.done()
    response = future.result()
    assert isinstance(response, AskUserResponse)
    assert [answer.scalar() for answer in response.answers] == ["Keep 2,000 rows"]
    assert response.answers[0].kind == "option"
    assert app._ask is None


def test_an_out_of_range_digit_is_swallowed_not_typed():
    app = make_app()
    event, future = ask_event(one_question())
    app._request_ask_user(event)
    app._handle_key("7")
    assert not future.done()
    assert app._input == ""


def test_enter_sends_what_was_typed_as_a_custom_answer():
    app = make_app()
    event, future = ask_event(one_question())
    app._request_ask_user(event)
    for char in "just the pinned ones":
        app._handle_key(" " if char == " " else char)
    app._handle_key("enter")
    response = future.result()
    assert response.answers[0].kind == "custom"
    assert response.answers[0].scalar() == "just the pinned ones"
    assert app._input == ""


def test_several_questions_answer_in_order_and_only_then_resolve():
    app = make_app()
    event, future = ask_event(*two_questions())
    app._request_ask_user(event)
    block = ask_block(app)
    app._handle_key("1")
    assert not future.done()
    assert block.at == 1
    assert block.items[0].answer == "Keep everything"
    app._handle_key("enter")
    assert future.done()
    assert len(future.result().answers) == 2


def test_esc_skips_the_rest_and_keeps_what_was_answered():
    app = make_app()
    event, future = ask_event(*two_questions())
    app._request_ask_user(event)
    block = ask_block(app)
    app._handle_key("1")
    app._handle_key("esc")
    response = future.result()
    assert [answer.scalar() for answer in response.answers] == ["Keep everything"]
    assert block.skipped


def test_esc_on_the_first_question_is_an_empty_response():
    app = make_app()
    event, future = ask_event(one_question())
    app._request_ask_user(event)
    app._handle_key("esc")
    assert future.result().is_empty


def test_the_placeholder_row_is_replaced_by_the_question():
    app = make_app()
    placeholder = blocks.ToolStatus(name="ask_user", status="running")
    group = blocks.ToolGroup(calls=[placeholder])
    app.viewport.add(group)
    app._tool_blocks["call_1"] = placeholder
    app._tool_groups["call_1"] = group

    event, _future = ask_event(one_question())
    app._request_ask_user(event)

    assert placeholder not in app.viewport.blocks
    assert group not in app.viewport.blocks
    assert isinstance(app.viewport.blocks[-1], blocks.AskUser)


def test_multi_select_digits_toggle_and_enter_confirms_the_set():
    app = make_app()
    event, future = ask_event(
        AskUserQuestion(
            question="What to include?",
            multi_select=True,
            options=[
                AskUserOption(label="docs"),
                AskUserOption(label="tests"),
                AskUserOption(label="ci"),
            ],
        )
    )
    app._request_ask_user(event)
    app._handle_key("1")
    app._handle_key("3")
    assert not future.done()
    app._handle_key("1")
    assert ask_current(app).checked == [2]
    app._handle_key("2")
    app._handle_key("enter")
    response = future.result()
    assert response.answers[0].kind == "multi"
    # Ticked labels come back in option order, not click order.
    assert response.answers[0].selected == ("tests", "ci")


def test_enter_with_no_options_and_no_text_does_nothing():
    app = make_app()
    event, future = ask_event(AskUserQuestion(question="Name it?"))
    app._request_ask_user(event)
    app._handle_key("enter")
    assert not future.done()
    assert app._ask is not None
