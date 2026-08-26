"""Questionnaire state machine behind the ``ask_user`` dialog.

Pure Python, no Textual imports: the TUI layer mutates this object from
keypresses and renders it. One dialog owns 1-4 questions, an optional
Submit review tab, and a collapsed mode.

Row model per question tab:

* one row per authored option,
* a ``Type something.`` free-text row appended to every question,
* a ``Next`` commit row appended to multi-select questions only.

Multi-question dialogs additionally get a Submit tab listing every
answer, warning about unanswered questions, and offering a
Submit answers / Cancel picker. Single-question dialogs finish as soon
as one answer is committed.

Keyboard contract:

* ``↑``/``↓`` (``k``/``j``) move between rows, wrapping at both ends.
* ``Enter`` confirms the focused option (single-select), toggles a
  checkbox (multi-select), commits typed text, or activates the focused
  Submit-picker row.
* ``Space`` toggles the focused checkbox (multi-select).
* ``Tab``/``Shift+Tab`` (``→``/``←``) switch tabs, wrapping.
* ``Ctrl+U`` clears the current custom-answer draft.
* ``Ctrl+]`` collapses/expands the dialog; while collapsed only
  ``Ctrl+]`` and ``Esc`` act.
* ``Esc`` cancels the whole questionnaire.
* Digits jump straight to a row.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vtx.core import AskUserAnswer, AskUserQuestion, AskUserResponse

OTHER_DISPLAY = "Type something."
NEXT_LABEL = "Next"
SUBMIT_PICK_LABEL = "Submit answers"
SUBMIT_PICK_CANCEL = "Cancel"
REVIEW_HEADING = "Review your answers"
INCOMPLETE_WARNING_PREFIX = "⚠ Answer remaining questions before submitting:"
NO_INPUT_PLACEHOLDER = "(no input)"

# Keys that collapse/expand the dialog. Terminals differ in how Ctrl+]
# is reported; accept both spellings.
COLLAPSE_KEYS = frozenset({"ctrl+]", "ctrl+right_square_bracket"})


@dataclass
class _QuestionState:
    """Per-question interactive state."""

    row: int = 0
    selected: int | None = None  # committed single-select option index
    toggled: set[int] = field(default_factory=set)  # multi-select checked indices
    draft: str = ""  # live custom-answer text while editing/browsing
    custom: str = ""  # committed custom answer


class AskUserDialog:
    """State machine for one ask_user questionnaire."""

    def __init__(self, questions: list[AskUserQuestion]) -> None:
        self.questions = list(questions)
        self._states = [_QuestionState() for _ in self.questions]
        self.is_multi_question = len(self.questions) > 1
        # Tab index: 0..len(questions)-1 are question tabs; the Submit
        # tab (multi-question dialogs only) sits at index len(questions).
        self.tab = 0
        self.submit_row = 0  # 0 = Submit answers, 1 = Cancel
        self.input_mode = False  # custom-answer inline input focused
        self.collapsed = False
        self.finished = False
        self.cancelled = False
        # Side effects the app applies after handle_key returns.
        self.pending_clear_draft = False

    # ------------------------------------------------------------------
    # Row model
    # ------------------------------------------------------------------

    @property
    def submit_tab_index(self) -> int:
        return len(self.questions)

    def is_on_submit_tab(self) -> bool:
        return self.is_multi_question and self.tab == self.submit_tab_index

    def rows(self, tab: int | None = None) -> list[tuple[str, int]]:
        """Rows of a question tab as ``(kind, option_index)`` pairs."""
        index = self.tab if tab is None else tab
        question = self.questions[index]
        rows = [("option", i) for i in range(len(question.options))]
        rows.append(("other", -1))
        if question.multi_select:
            rows.append(("next", -1))
        return rows

    def row_kind(self, row: int | None = None) -> tuple[str, int]:
        rows = self.rows()
        row = self.current_state().row if row is None else row
        return rows[max(0, min(row, len(rows) - 1))]

    def current_state(self) -> _QuestionState:
        index = min(self.tab, len(self.questions) - 1)
        return self._states[index]

    # ------------------------------------------------------------------
    # Answers
    # ------------------------------------------------------------------

    def answered_indices(self) -> list[int]:
        answered: list[int] = []
        for i, state in enumerate(self._states):
            question = self.questions[i]
            if question.multi_select:
                if state.toggled or state.custom.strip():
                    answered.append(i)
            elif state.selected is not None or state.custom.strip():
                answered.append(i)
        return answered

    def unanswered_questions(self) -> list[str]:
        answered = set(self.answered_indices())
        return [q.question for i, q in enumerate(self.questions) if i not in answered]

    def build_answers(self) -> tuple[AskUserAnswer, ...]:
        """Resolved answers in question order; unanswered questions are omitted."""
        answers: list[AskUserAnswer] = []
        for question, state in zip(self.questions, self._states, strict=True):
            kind: str
            answer: str | None = None
            selected: tuple[str, ...] = ()
            preview: str | None = None
            if question.multi_select:
                # Checked boxes win over a typed custom answer; the
                # checkbox rows are the primary affordance in multi mode.
                if state.toggled:
                    kind = "multi"
                    ordered = sorted(state.toggled)
                    selected = tuple(question.options[j].label for j in ordered)
                elif state.custom.strip():
                    kind = "custom"
                    answer = state.custom.strip()
                else:
                    continue
            elif state.selected is not None:
                option = question.options[state.selected]
                kind = "option"
                answer = option.label
                preview = option.preview or None
            elif state.custom.strip():
                kind = "custom"
                answer = state.custom.strip()
            else:
                continue
            answers.append(
                AskUserAnswer(
                    question=question.question,
                    kind=kind,
                    answer=answer,
                    selected=selected,
                    preview=preview,
                )
            )
        return tuple(answers)

    def build_response(self) -> AskUserResponse:
        return AskUserResponse(answers=self.build_answers())

    def has_outstanding_content(self) -> bool:
        """True when anything (answers, drafts) would be lost."""
        if self.answered_indices():
            return True
        return any(bool(s.draft.strip()) for s in self._states)

    # ------------------------------------------------------------------
    # Key handling
    # ------------------------------------------------------------------

    def handle_key(self, key: str, *, custom_value: str = "") -> bool:
        """Apply a keypress; returns True when the key was consumed.

        ``custom_value`` carries the live text of the inline input so
        the state machine can persist drafts when focus moves away.
        """
        if key in COLLAPSE_KEYS:
            self.collapsed = not self.collapsed
            return True

        if self.collapsed:
            # While collapsed every keystroke other than cancel is
            # ignored, so invisible answers cannot mutate.
            if key == "escape":
                self.cancelled = True
                self.finished = True
                return True
            return False

        if key == "escape":
            self.cancelled = True
            self.finished = True
            return True

        if self.is_on_submit_tab():
            return self._handle_submit_tab_key(key)

        return self._handle_question_key(key, custom_value=custom_value)

    def _handle_question_key(self, key: str, *, custom_value: str = "") -> bool:
        state = self.current_state()
        question = self.questions[self.tab]
        row_count = len(self.rows())

        if key in ("up", "k"):
            self._sync_draft(custom_value)
            self.input_mode = False
            state.row = (state.row - 1) % row_count
            return True
        if key in ("down", "j"):
            self._sync_draft(custom_value)
            self.input_mode = False
            state.row = (state.row + 1) % row_count
            return True
        if key in ("tab", "right", "shift+tab", "left"):
            self._sync_draft(custom_value)
            return self._switch_tab(-1 if key in ("shift+tab", "left") else 1)

        if key.isdigit() and len(key) == 1:
            number = int(key)
            if number < 1 or number > row_count:
                return False
            state.row = number - 1
            kind, index = self.row_kind()
            if kind == "option":
                return self._activate_option(index, question, state)
            if kind == "other":
                self.input_mode = True
            elif kind == "next":
                self._commit_and_advance(state, custom_value)
            return True

        if key == " ":
            kind, index = self.row_kind()
            if question.multi_select and kind == "option":
                self._toggle(index, state)
                return True
            return False

        if key == "ctrl+u":
            state.draft = ""
            state.custom = ""
            self.pending_clear_draft = True
            return True

        if key == "enter":
            kind, index = self.row_kind()
            if kind == "option":
                return self._activate_option(index, question, state)
            if kind == "other":
                self._sync_draft(custom_value)
                if state.draft.strip():
                    self._commit_and_advance(state, custom_value)
                else:
                    self.input_mode = True
                return True
            if kind == "next":
                self._commit_and_advance(state, custom_value)
                return True
        return False

    def _handle_submit_tab_key(self, key: str) -> bool:
        if key in ("up", "k", "down", "j"):
            self.submit_row = (self.submit_row + 1) % 2
            return True
        if key in ("tab", "right"):
            return self._switch_tab(1)
        if key in ("shift+tab", "left"):
            return self._switch_tab(-1)
        if key == "enter":
            self._activate_submit_row()
            return True
        if key == " ":
            return False
        if key.isdigit() and key in ("1", "2"):
            self.submit_row = int(key) - 1
            self._activate_submit_row()
            return True
        return False

    def _activate_option(
        self, index: int, question: AskUserQuestion, state: _QuestionState
    ) -> bool:
        if question.multi_select:
            self._toggle(index, state)
            return True
        state.selected = index
        self._commit_and_advance(state, "")
        return True

    def _toggle(self, index: int, state: _QuestionState) -> None:
        if index in state.toggled:
            state.toggled.discard(index)
        else:
            state.toggled.add(index)

    def _activate_submit_row(self) -> None:
        if self.submit_row == 0:
            self.finished = True
        else:
            self.cancelled = True
            self.finished = True

    def _commit_and_advance(self, state: _QuestionState, custom_value: str) -> None:
        """Persist the focused row's answer and move forward."""
        kind = self.row_kind()[0]
        self._sync_draft(custom_value)
        if kind == "other" and state.draft.strip():
            state.custom = state.draft.strip()
            state.draft = ""
            state.selected = None
        elif kind == "option":
            state.custom = ""
        state.row = min(state.row, len(self.rows()) - 1)
        self.input_mode = False
        if not self.is_multi_question:
            # A single-question dialog resolves as soon as one answer
            # is committed; there is no Submit tab to visit.
            self.finished = True
            return
        if self.tab < self.submit_tab_index:
            self.tab += 1

    def _switch_tab(self, delta: int) -> bool:
        if not self.is_multi_question:
            return False
        total_tabs = len(self.questions) + 1
        self.tab = (self.tab + delta) % total_tabs
        self.input_mode = False
        return True

    def _sync_draft(self, custom_value: str) -> None:
        """Mirror the inline input's live text into the focused question."""
        if custom_value:
            self.current_state().draft = custom_value

    def commit_custom(self, text: str) -> None:
        """Commit the custom-answer input (Enter inside the inline input)."""
        if self.is_on_submit_tab():
            return
        state = self.current_state()
        cleaned = text.strip()
        if not cleaned:
            return
        state.custom = cleaned
        state.draft = ""
        state.selected = None
        self.input_mode = False
        if not self.is_multi_question:
            self.finished = True
            return
        if self.tab < self.submit_tab_index:
            self.tab += 1
