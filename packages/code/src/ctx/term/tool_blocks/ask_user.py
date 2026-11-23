"""The in-chat ``ask_user`` tool block.

The questionnaire state machine (:mod:`ctx.term.ask_user`) owns the answers;
this block owns the pixels. It renders the bordered dialog into the tool
body, syncs the inline free-text input with the dialog's draft, and moves
focus between them. The generic ``ToolBlock`` has none of that knowledge.
"""

from __future__ import annotations

import contextlib
import textwrap

from ctx.core.config import config
from ctx.term.ask_user import (
    INCOMPLETE_WARNING_PREFIX,
    NEXT_LABEL,
    NO_INPUT_PLACEHOLDER,
    OTHER_DISPLAY,
    REVIEW_HEADING,
    SUBMIT_PICK_CANCEL,
    SUBMIT_PICK_LABEL,
    AskUserDialog,
)
from ctx.term.input import AskUserInput  # ty: ignore[import-not-found]
from rich.style import Style
from rich.text import Text
from textual.widgets import Label

from .base import CodingToolBlock

ACTIVE_POINTER = "❯ "  # noqa: RUF001 - intentional pointer glyph
INACTIVE_POINTER = "  "
CHECKED_BOX = "[✔]"
UNCHECKED_BOX = "[ ]"
CONFIRMED_MARK = " ✔"
NUMBER_SEPARATOR = ". "
CONTINUATION_INDENT = "  "

# Footer hint fragments; joined with " · " into the footer line.
HINT_ENTER = "enter to select"
HINT_NAV = "↑/↓ to navigate"
HINT_TOGGLE = "space to toggle"
HINT_TAB = "tab to switch questions"
HINT_CANCEL = "esc to cancel"
HINT_SAVE_DRAFT = "enter to save"
HINT_CLEAR = "ctrl+u to clear"
HINT_COLLAPSE = "ctrl+] to collapse"
HINT_EXPAND = "ctrl+] to expand"

# Snug box width bounds for the bordered questionnaire.
MIN_DIALOG_WIDTH = 48
MAX_DIALOG_WIDTH = 100


class AskUserToolBlock(CodingToolBlock):
    """Bordered questionnaire rendered into the tool body."""

    is_ask_user = True

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ask_dialog: AskUserDialog | None = None
        self._ask_user_input_visible: bool = False

    def _refresh_box(self) -> None:
        # The dialog state can arrive before Textual has mounted this block's
        # children. Re-paint it on mount just like the generic surfaces.
        super()._refresh_box()
        if self._ask_dialog is not None:
            self._safe_update(self._render_ask_user_output)
            self._sync_ask_user_widgets()

    # -- ask_user rendering ---------------------------------------------------

    @property
    def is_awaiting_ask_user(self) -> bool:
        return self._ask_dialog is not None or self.has_class("-ask-user")

    def show_ask_user(self, dialog: AskUserDialog) -> None:
        """Attach the questionnaire dialog and render it into the block.

        Safe to call before the widget is mounted — the DOM updates
        become no-ops, which keeps unit tests from needing a live app.
        """
        self._ask_dialog = dialog
        self.add_class("-ask-user")
        self._set_state(None)
        # Best effort now; ``_refresh_box`` repaints after mount when the
        # block's children did not exist yet (see its docstring).
        self._safe_update(
            lambda: self.query_one("#tool-header", Label).update(self._format_header())
        )
        self._safe_update(self._render_ask_user_output)
        self._sync_ask_user_widgets()

    def rerender_ask_user(self) -> None:
        """Re-render the dialog from current state machine contents."""
        if self._ask_dialog is None:
            return
        self._safe_update(self._render_ask_user_output)
        self._sync_ask_user_widgets()

    def hide_ask_user(self) -> None:
        self._ask_dialog = None
        self.remove_class("-ask-user")
        self._set_state(None)
        self._safe_update(
            lambda: self.query_one("#tool-header", Label).update(self._format_header())
        )
        self._sync_ask_user_widgets()
        self._safe_update(self._hide_ask_user_output)

    # Inline input accessors used by the app layer -----------------------------

    def ask_user_custom_value(self) -> str:
        with contextlib.suppress(Exception):
            return self.query_one("#ask-user-input", AskUserInput).value
        return ""

    def set_ask_user_custom_value(self, value: str) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#ask-user-input", AskUserInput).value = value

    def _hide_ask_user_output(self) -> None:
        try:
            output = self.query_one("#tool-output", Label)
        except Exception:
            return
        self.remove_class("-with-details")
        output.remove_class("-details")
        output.add_class("-hidden")
        output.update(Text(""))

    def _safe_update(self, fn) -> None:
        """Run ``fn`` and swallow DOM errors (e.g. widget not yet mounted)."""
        with contextlib.suppress(Exception):
            fn()

    def _render_ask_user_output(self) -> None:
        try:
            output = self.query_one("#tool-output", Label)
        except Exception:
            return
        if self._ask_dialog is None:
            return
        self.remove_class("-with-details")
        output.remove_class("-hidden")
        output.remove_class("-details")
        output.update(self.format_ask_user_dialog())

    # Dialog drawing ------------------------------------------------------------

    def _dialog_width(self) -> int:
        try:
            term_width = self.app.size.width
        except Exception:
            term_width = 0
        if term_width <= 0:
            return 96
        return max(MIN_DIALOG_WIDTH, min(MAX_DIALOG_WIDTH, term_width - 4))

    def format_ask_user_dialog(self) -> Text:
        """The bordered questionnaire (or its collapsed hint row)."""
        assert self._ask_dialog is not None
        colors = config.ui.colors
        if self._ask_dialog.collapsed:
            return Text(f"  {HINT_EXPAND} · {HINT_CANCEL}", style=Style(color=colors.dim))

        width = self._dialog_width()
        body = self._build_dialog_body(self._ask_dialog, width)
        inner = max(line.cell_len for line in body)

        result = Text()
        result.append(f"╭{'─' * (inner + 2)}╮\n", style=Style(color=colors.accent))
        for line in body:
            result.append("│ ")
            result.append_text(line)
            padding = inner - line.cell_len
            if padding > 0:
                result.append(" " * padding)
            result.append(" │\n", style=Style(color=colors.accent))
        result.append(f"╰{'─' * (inner + 2)}╯", style=Style(color=colors.accent))

        hints = self._format_hint_line(self._ask_dialog)
        if hints.plain.strip():
            result.append("\n  ")
            result.append_text(hints)
        return result

    def _build_dialog_body(self, dialog: AskUserDialog, width: int) -> list[Text]:
        lines: list[Text] = []
        if dialog.is_multi_question:
            lines.append(self._format_tab_bar(dialog))
            lines.append(Text())
        if dialog.is_on_submit_tab():
            lines.extend(self._format_submit_body(dialog))
        else:
            lines.extend(self._format_question_body(dialog, width))
        lines.append(Text())
        return lines

    def _format_tab_bar(self, dialog: AskUserDialog) -> Text:
        colors = config.ui.colors
        answered = set(dialog.answered_indices())
        row = Text()
        row.append(" ← ", style=Style(color=colors.dim))
        for i, question in enumerate(dialog.questions):
            label = question.header or f"Q{i + 1}"
            box = "■" if i in answered else "□"
            if i == dialog.tab:
                style: Style | str = Style(bgcolor=colors.accent, color=colors.bg, bold=True)
            elif i in answered:
                style = Style(color=colors.success)
            else:
                style = Style(color=colors.muted)
            row.append(f" {box} {label} ", style=style)
            row.append(" ", style=Style(color=colors.dim))
        all_answered = len(answered) == len(dialog.questions)
        on_submit = dialog.is_on_submit_tab()
        submit_style: Style | str = (
            Style(bgcolor=colors.accent, color=colors.bg, bold=True)
            if on_submit
            else Style(color=colors.success if all_answered else colors.dim)
        )
        row.append(" ✓ Submit ", style=submit_style)
        row.append(" → ", style=Style(color=colors.dim))
        return row

    def _format_question_body(self, dialog: AskUserDialog, width: int) -> list[Text]:
        colors = config.ui.colors
        question = dialog.questions[dialog.tab]
        state = dialog.current_state()
        content_width = max(20, width - 6)

        lines: list[Text] = []
        for segment in textwrap.wrap(question.question, content_width) or [""]:
            lines.append(Text(segment, style=Style(color=colors.fg, bold=True)))
        lines.append(Text())

        rows = dialog.rows()
        number_width = len(str(len(rows)))
        active_preview: str | None = None

        for row_index, (kind, option_index) in enumerate(rows):
            is_active = row_index == state.row
            pointer = ACTIVE_POINTER if is_active else INACTIVE_POINTER
            number = f"{row_index + 1}{NUMBER_SEPARATOR}".rjust(number_width + 2)

            if kind == "option":
                option = question.options[option_index]
                checked = option_index in state.toggled
                confirmed = not question.multi_select and state.selected == option_index
                label_style = Style(color=colors.accent, bold=True) if is_active else Style()
                line = Text()
                line.append(pointer, style=label_style if is_active else Style(color=colors.dim))
                line.append(number, style=label_style if is_active else Style(color=colors.fg))
                if question.multi_select:
                    box = CHECKED_BOX if checked else UNCHECKED_BOX
                    line.append(
                        box + " ", style=Style(color=colors.accent if checked else colors.muted)
                    )
                line.append(
                    option.label + (CONFIRMED_MARK if confirmed else ""), style=label_style
                )
                lines.append(line)
                if option.description:
                    for segment in textwrap.wrap(option.description, content_width - 2) or [""]:
                        lines.append(
                            Text(CONTINUATION_INDENT + segment, style=Style(color=colors.dim))
                        )
                if is_active and option.preview:
                    active_preview = option.preview
            elif kind == "other":
                draft = state.draft.strip()
                if draft:
                    # A fresh draft shows unconfirmed; a draft equal to
                    # the committed answer keeps its ✔ mark below.
                    label = draft
                    confirmed_custom = bool(state.custom.strip()) and draft == state.custom.strip()
                else:
                    label = OTHER_DISPLAY
                    confirmed_custom = False
                label_style = (
                    Style(color=colors.accent, bold=True) if is_active else Style(color=colors.fg)
                )
                line = Text()
                line.append(pointer, style=label_style if is_active else Style(color=colors.dim))
                line.append(number, style=label_style if is_active else Style(color=colors.fg))
                if question.multi_select:
                    line.append(UNCHECKED_BOX + " ", style=Style(color=colors.muted))
                line.append(
                    label + (CONFIRMED_MARK if confirmed_custom else ""), style=label_style
                )
                lines.append(line)
            else:  # next — commit row for multi-select questions
                label_style = (
                    Style(color=colors.accent, bold=True) if is_active else Style(color=colors.fg)
                )
                line = Text()
                line.append(pointer, style=label_style if is_active else Style(color=colors.dim))
                line.append(NEXT_LABEL, style=label_style)
                lines.append(line)

        if active_preview:
            lines.extend(self._format_preview_box(active_preview, width))
        return lines

    def _format_preview_box(self, preview: str, width: int) -> list[Text]:
        colors = config.ui.colors
        border_style = Style(color=colors.dim)
        inner_width = max(16, min(72, width - 10))
        wrapped: list[str] = []
        for raw_line in preview.splitlines() or [""]:
            wrapped.extend(textwrap.wrap(raw_line, inner_width) or [""])
            if len(wrapped) >= 12:
                break
        if len(wrapped) >= 12:
            wrapped = wrapped[:12]
            if wrapped:
                wrapped[-1] = wrapped[-1][: max(0, inner_width - 1)] + "…"
        lines = [Text("  ┌" + "─" * (inner_width + 2) + "┐", style=border_style)]
        for segment in wrapped:
            pad = " " * (inner_width - len(segment))
            lines.append(Text("  │ " + segment + pad + " │", style=Style(color=colors.muted)))
        lines.append(Text("  └" + "─" * (inner_width + 2) + "┘", style=border_style))
        return lines

    def _question_answer_scalar(self, dialog: AskUserDialog, index: int) -> str | None:
        question = dialog.questions[index]
        state = dialog._states[index]
        if question.multi_select:
            if state.toggled:
                ordered = sorted(state.toggled)
                return ", ".join(question.options[j].label for j in ordered)
            if state.custom.strip():
                return state.custom.strip()
            return None
        if state.selected is not None:
            return question.options[state.selected].label
        if state.custom.strip():
            return state.custom.strip()
        return None

    def _format_submit_body(self, dialog: AskUserDialog) -> list[Text]:
        colors = config.ui.colors
        lines: list[Text] = [Text(REVIEW_HEADING, style=Style(color=colors.fg, bold=True)), Text()]
        for i, question in enumerate(dialog.questions):
            header = question.header or question.question
            if len(header) > 32:
                header = header[:29] + "..."
            answer = self._question_answer_scalar(dialog, i)
            entry = Text(f"{header}: ")
            if answer is None:
                entry.append(NO_INPUT_PLACEHOLDER, style=Style(color=colors.dim))
            else:
                entry.append(answer)
            lines.append(entry)
        unanswered = []
        for i, question in enumerate(dialog.questions):
            if self._question_answer_scalar(dialog, i) is None:
                unanswered.append(question.header or question.question)
        if unanswered:
            warning = Text(INCOMPLETE_WARNING_PREFIX, style=Style(color=colors.notice))
            warning.append(" " + ", ".join(unanswered), style=Style(color=colors.notice))
            lines.append(warning)
        lines.append(Text())
        for i, label in enumerate((SUBMIT_PICK_LABEL, SUBMIT_PICK_CANCEL)):
            active = dialog.submit_row == i
            pointer = ACTIVE_POINTER if active else INACTIVE_POINTER
            style = Style(color=colors.accent, bold=True) if active else Style(color=colors.fg)
            line = Text()
            line.append(pointer, style=style)
            line.append(f"{i + 1}{NUMBER_SEPARATOR}", style=style)
            line.append(label, style=style)
            lines.append(line)
        return lines

    def _format_hint_line(self, dialog: AskUserDialog) -> Text:
        colors = config.ui.colors
        if dialog.collapsed:
            parts = [HINT_EXPAND, HINT_CANCEL]
        elif dialog.input_mode:
            parts = [HINT_SAVE_DRAFT, HINT_NAV, HINT_CLEAR, HINT_CANCEL]
        elif dialog.is_on_submit_tab():
            parts = ["enter to confirm", HINT_NAV, HINT_CANCEL]
        else:
            parts = [HINT_ENTER, HINT_NAV]
            if dialog.questions[min(dialog.tab, len(dialog.questions) - 1)].multi_select:
                parts.append(HINT_TOGGLE)
            if dialog.is_multi_question:
                parts.append(HINT_TAB)
            parts.append(HINT_CANCEL)
            parts.append(HINT_COLLAPSE)
        return Text("  " + " · ".join(parts), style=Style(color=colors.dim))

    # Inline widget sync --------------------------------------------------------

    def _sync_ask_user_widgets(self) -> None:
        """Show/hide + focus the inline input to match dialog state."""
        dialog = self._ask_dialog
        custom_visible = bool(dialog and not dialog.collapsed and dialog.input_mode)
        with contextlib.suppress(Exception):
            custom_input = self.query_one("#ask-user-input", AskUserInput)
            custom_input.display = custom_visible
            if custom_visible and dialog is not None:
                draft = dialog.current_state().draft
                if custom_input.value != draft:
                    custom_input.value = draft
        focus_changed = custom_visible != self._ask_user_input_visible
        self._ask_user_input_visible = custom_visible
        if focus_changed:
            # Move focus after the DOM has caught up with the visibility
            # change so the user can actually type into the field.
            self.call_after_refresh(self._sync_ask_user_focus)

    def _sync_ask_user_focus(self) -> None:
        with contextlib.suppress(Exception):
            if self._ask_user_input_visible:
                self.query_one("#ask-user-input", AskUserInput).focus()
            else:
                # Return focus to the chat input box so picker keys
                # (digits/arrows) keep working after the user navigates
                # away from an inline field.
                self.app.query_one("#input-box").focus()
