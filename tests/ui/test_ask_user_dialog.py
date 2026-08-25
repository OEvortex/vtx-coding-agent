"""Tests for the rpiv-style ask_user questionnaire state machine."""

from vtx.core import AskUserOption, AskUserQuestion
from vtx.tui.ask_user import AskUserDialog


def _q(
    question: str = "Pick one?",
    header: str = "H",
    multi: bool = False,
    n_options: int = 2,
    previews: bool = False,
) -> AskUserQuestion:
    return AskUserQuestion(
        question=question,
        header=header,
        options=[
            AskUserOption(
                label=f"o{i}", description=f"desc {i}", preview=f"preview {i}" if previews else ""
            )
            for i in range(n_options)
        ],
        multi_select=multi,
    )


def _single(multi: bool = False, n_options: int = 2) -> AskUserDialog:
    return AskUserDialog([_q(multi=multi, n_options=n_options)])


def _multi_question() -> AskUserDialog:
    return AskUserDialog(
        [_q(question="First?", header="One"), _q(question="Second?", header="Two", multi=True)]
    )


# ------------------------------------------------------------------
# Single-select basics
# ------------------------------------------------------------------


def test_single_select_enter_commits_and_finishes():
    dialog = _single()
    assert dialog.handle_key("enter") is True
    assert dialog.finished is True
    assert not dialog.cancelled
    answers = dialog.build_answers()
    assert len(answers) == 1
    assert answers[0].kind == "option"
    assert answers[0].answer == "o0"


def test_single_select_number_key_picks_option():
    dialog = _single(n_options=3)
    dialog.handle_key("3")
    assert dialog.finished
    assert dialog.build_answers()[0].answer == "o2"


def test_single_select_out_of_range_number_not_consumed():
    dialog = _single(n_options=2)
    assert dialog.handle_key("5") is False
    assert not dialog.finished


def test_arrows_wrap_within_rows():
    dialog = _single(n_options=2)
    # rows: o1, o2, Type something.
    dialog.handle_key("up")
    kind, index = dialog.row_kind()
    assert (kind, index) == ("other", -1)
    dialog.handle_key("down")
    assert dialog.row_kind() == ("option", 0)


def test_custom_answer_via_other_row():
    dialog = _single()
    dialog.handle_key("down", custom_value="")
    dialog.handle_key("down", custom_value="")  # onto Type something.
    dialog.handle_key("enter")  # empty draft -> opens input mode
    assert dialog.input_mode is True
    assert not dialog.finished
    dialog.commit_custom("my own words")
    assert dialog.finished
    answers = dialog.build_answers()
    assert answers[0].kind == "custom"
    assert answers[0].answer == "my own words"


def test_enter_on_other_with_text_commits_directly():
    dialog = _single()
    dialog.handle_key("down")
    dialog.handle_key("down")
    dialog.handle_key("enter", custom_value="typed answer")
    assert dialog.finished
    assert dialog.build_answers()[0].answer == "typed answer"


def test_draft_survives_browsing_other_rows():
    dialog = _single()
    dialog.handle_key("down", custom_value="")
    dialog.handle_key("enter")  # input mode on other row
    # User types then navigates away; the app passes the live value in.
    dialog.handle_key("down", custom_value="keep me")
    assert dialog.input_mode is False
    assert dialog.current_state().draft == "keep me"
    dialog.handle_key("up", custom_value="")
    assert dialog.current_state().draft == "keep me"


def test_ctrl_u_clears_draft_and_committed_custom():
    dialog = _single()
    dialog.handle_key("down")
    dialog.handle_key("enter", custom_value="temp")
    # Single-question commit finished the dialog; start a fresh one for
    # the clear path.
    dialog = _single()
    dialog.current_state().draft = "draft text"
    dialog.handle_key("ctrl+u")
    assert dialog.pending_clear_draft is True
    assert dialog.current_state().draft == ""


# ------------------------------------------------------------------
# Multi-select
# ------------------------------------------------------------------


def test_space_toggles_checkbox():
    dialog = _single(multi=True)
    dialog.handle_key(" ")
    assert 0 in dialog.current_state().toggled
    dialog.handle_key(" ")
    assert 0 not in dialog.current_state().toggled


def test_multi_enter_on_option_toggles_not_submits():
    dialog = _single(multi=True)
    dialog.handle_key("down")  # row 1
    dialog.handle_key("enter")
    assert 1 in dialog.current_state().toggled
    assert not dialog.finished


def test_next_row_commits_and_advances():
    dialog = AskUserDialog([_q(multi=True, n_options=2)])
    dialog.handle_key(" ")  # toggle o0
    dialog.handle_key("down")  # option o1
    dialog.handle_key("down")  # Type something.
    dialog.handle_key("down")  # Next
    assert dialog.row_kind() == ("next", -1)
    dialog.handle_key("enter")
    assert dialog.is_on_submit_tab() is False  # single-question dialog finishes instead


def test_next_row_finishes_single_question_dialog():
    dialog = _single(multi=True)
    dialog.handle_key(" ")  # toggle o0
    dialog.handle_key("up")  # wraps to Next
    assert dialog.row_kind() == ("next", -1)
    dialog.handle_key("enter")
    assert dialog.finished
    answers = dialog.build_answers()
    assert answers[0].kind == "multi"
    assert answers[0].selected == ("o0",)


def test_multi_number_keys_toggle():
    dialog = _single(multi=True, n_options=3)
    dialog.handle_key("2")
    assert 1 in dialog.current_state().toggled


def test_multi_custom_answer_used_when_nothing_toggled():
    dialog = _single(multi=True)
    dialog.handle_key("down")  # other row
    dialog.commit_custom("freeform choice")
    answers = dialog.build_answers()
    assert answers[0].kind == "custom"
    assert answers[0].answer == "freeform choice"


# ------------------------------------------------------------------
# Notes
# ------------------------------------------------------------------


def test_n_opens_notes_editor_and_enter_saves():
    dialog = _single()
    dialog.handle_key("enter")  # answer with o0 first
    dialog.tab = 0
    dialog.handle_key("n")
    assert dialog.notes_open is True
    assert dialog.notes_for_global is False
    dialog.handle_key("enter", notes_value="prefer uv")
    assert dialog.notes_open is False
    assert dialog.current_state().note == "prefer uv"
    answers = dialog.build_answers()
    assert answers[0].notes == "prefer uv"


def test_note_alone_does_not_answer_question():
    dialog = _single()
    dialog.handle_key("n")
    dialog.handle_key("enter", notes_value="thinking")
    assert dialog.answered_indices() == []


def test_escape_closes_notes_before_cancelling():
    dialog = _single()
    dialog.handle_key("n")
    dialog.handle_key("escape", notes_value="saved note")
    assert dialog.notes_open is False
    assert not dialog.cancelled
    dialog.handle_key("escape")
    assert dialog.cancelled is True


def test_submit_tab_n_opens_global_note():
    dialog = _multi_question()
    dialog.tab = dialog.submit_tab_index
    dialog.handle_key("n")
    assert dialog.notes_open is True
    assert dialog.notes_for_global is True
    dialog.save_note("overall guidance")
    assert dialog.global_note_text() == "overall guidance"
    response = dialog.build_response()
    assert response.global_note == "overall guidance"


# ------------------------------------------------------------------
# Multi-question tabs and Submit tab
# ------------------------------------------------------------------


def test_tab_switching_wraps_including_submit_tab():
    dialog = _multi_question()
    assert dialog.is_multi_question is True
    dialog.handle_key("tab")  # One -> Two
    assert dialog.tab == 1
    dialog.handle_key("tab")  # Two -> Submit
    assert dialog.is_on_submit_tab() is True
    dialog.handle_key("tab")  # Submit -> One
    assert dialog.tab == 0
    dialog.handle_key("shift+tab")  # One -> Submit
    assert dialog.is_on_submit_tab() is True


def test_tab_switch_disabled_for_single_question():
    dialog = _single()
    assert dialog.handle_key("tab") is False


def test_commit_first_question_advances_to_second():
    dialog = _multi_question()
    dialog.handle_key("enter")  # commit o0 on tab 0
    assert dialog.tab == 1
    assert not dialog.finished
    assert dialog.answered_indices() == [0]


def test_submit_picker_submits_everything():
    dialog = _multi_question()
    dialog.handle_key("enter")  # q1 answered with o0
    dialog.handle_key(" ")  # q2 toggle o0
    dialog.handle_key("down")  # option o1
    dialog.handle_key("down")  # Type something.
    dialog.handle_key("down")  # Next
    dialog.handle_key("enter")  # commit -> submit tab
    assert dialog.is_on_submit_tab() is True
    dialog.handle_key("enter")  # Submit answers focused by default
    assert dialog.finished and not dialog.cancelled
    response = dialog.build_response()
    assert response.answers[0].answer == "o0"
    assert response.answers[1].kind == "multi"
    assert response.answers[1].selected == ("o0",)


def test_cancel_row_declines():
    dialog = _multi_question()
    dialog.handle_key("enter")  # answer q1
    dialog.handle_key("tab")  # to q2
    dialog.handle_key("shift+tab")  # back to submit? no: q1... wrap from q2-1
    # Move to the submit tab directly.
    dialog.tab = dialog.submit_tab_index
    dialog.submit_row = 1
    dialog.handle_key("enter")
    assert dialog.cancelled is True


def test_unanswered_questions_listed():
    dialog = _multi_question()
    assert set(dialog.unanswered_questions()) == {"First?", "Second?"}
    dialog.handle_key("enter")
    assert dialog.unanswered_questions() == ["Second?"]


def test_partial_submission_allowed():
    dialog = _multi_question()
    dialog.handle_key("enter")  # only first question answered
    dialog.tab = dialog.submit_tab_index
    dialog.handle_key("enter")
    assert dialog.finished
    answers = dialog.build_response().answers
    assert len(answers) == 1
    assert answers[0].question == "First?"


# ------------------------------------------------------------------
# Collapse mode
# ------------------------------------------------------------------


def test_collapse_hides_and_ignores_keys_except_cancel():
    dialog = _single()
    assert dialog.handle_key("ctrl+]") is True
    assert dialog.collapsed is True
    # Keys that would mutate answers are ignored while collapsed.
    assert dialog.handle_key("enter") is False
    assert dialog.handle_key(" ") is False
    assert not dialog.finished
    # Expand again.
    dialog.handle_key("ctrl+]")
    assert dialog.collapsed is False
    dialog.handle_key("enter")
    assert dialog.finished


def test_escape_works_while_collapsed():
    dialog = _single()
    dialog.handle_key("ctrl+]")
    dialog.handle_key("escape")
    assert dialog.cancelled is True


def test_collapse_accepts_alternate_key_name():
    dialog = _single()
    dialog.handle_key("ctrl+right_square_bracket")
    assert dialog.collapsed is True


# ------------------------------------------------------------------
# Answers / envelope data
# ------------------------------------------------------------------


def test_preview_rides_the_chosen_answer():
    dialog = AskUserDialog([_q(previews=True)])
    dialog.handle_key("enter")  # pick o0 which carries a preview
    answers = dialog.build_answers()
    assert answers[0].preview == "preview 0"


def test_has_outstanding_content_detects_progress():
    dialog = _multi_question()
    assert dialog.has_outstanding_content() is False
    dialog.current_state().draft = "half-typed"
    assert dialog.has_outstanding_content() is True
