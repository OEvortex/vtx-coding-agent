"""Tests for the rpiv-style ask_user rendering on the ToolBlock.

These tests don't spin up a Textual app unless focus behaviour needs
one — they exercise the data-shaping and rendering helpers directly,
plus the public show/rerender/hide API that the agent runner calls.
"""

import pytest
from textual.app import App, ComposeResult

from vtx.core import AskUserOption, AskUserQuestion
from vtx.tui.ask_user import (
    NEXT_LABEL,
    OTHER_DISPLAY,
    REVIEW_HEADING,
    SUBMIT_PICK_CANCEL,
    SUBMIT_PICK_LABEL,
    AskUserDialog,
)
from vtx.tui.blocks import ToolBlock
from vtx.tui.chat import ChatLog
from vtx.tui.input import AskUserInput
from vtx.tui.styles import get_styles


def _q(
    question: str = "Which task are we planning?",
    header: str = "Feature Type",
    multi: bool = False,
    previews: bool = False,
) -> AskUserQuestion:
    return AskUserQuestion(
        question=question,
        header=header,
        options=[
            AskUserOption(
                label=f"option-{i}",
                description=f"desc {i}",
                preview=f"preview {i}" if previews else "",
            )
            for i in range(2)
        ],
        multi_select=multi,
    )


def _block_with_dialog(dialog: AskUserDialog) -> ToolBlock:
    block = ToolBlock(name="ask_user", call_msg="question")
    block.show_ask_user(dialog=dialog)
    return block


class TestToolBlockAskUserState:
    def test_initial_state_is_idle(self):
        block = ToolBlock(name="ask_user", call_msg="question")
        assert block._ask_dialog is None
        assert block.is_awaiting_ask_user is False
        assert not block.has_class("-ask-user")

    def test_show_ask_user_marks_block(self):
        dialog = AskUserDialog([_q()])
        block = _block_with_dialog(dialog)
        assert block.is_awaiting_ask_user is True
        assert block.has_class("-ask-user")
        assert block._ask_dialog is dialog

    def test_hide_ask_user_clears_state(self):
        block = _block_with_dialog(AskUserDialog([_q()]))
        block.hide_ask_user()
        assert block.is_awaiting_ask_user is False
        assert not block.has_class("-ask-user")
        assert block._ask_dialog is None


class TestDialogBoxRendering:
    def test_box_borders_present(self):
        block = _block_with_dialog(AskUserDialog([_q()]))
        text = block.format_ask_user_dialog()
        plain = text.plain
        assert plain.startswith("╭")
        assert "╰" in plain
        assert "│" in plain

    def test_question_and_options_render(self):
        block = _block_with_dialog(AskUserDialog([_q()]))
        plain = block.format_ask_user_dialog().plain
        assert "Which task are we planning?" in plain
        assert "option-0" in plain
        assert "desc 1" in plain

    def test_type_something_row_appended(self):
        block = _block_with_dialog(AskUserDialog([_q()]))
        plain = block.format_ask_user_dialog().plain
        assert OTHER_DISPLAY in plain

    def test_active_row_pointer(self):
        block = _block_with_dialog(AskUserDialog([_q()]))
        plain = block.format_ask_user_dialog().plain
        assert "❯" in plain  # noqa: RUF001 - intentional rpiv pointer glyph

    def test_confirmed_mark_on_committed_option(self):
        dialog = AskUserDialog([_q()])
        dialog.current_state().selected = 0
        block = _block_with_dialog(dialog)
        assert "✔" in block.format_ask_user_dialog().plain

    def test_multi_select_checkboxes(self):
        dialog = AskUserDialog([_q(multi=True)])
        dialog.current_state().toggled.add(0)
        block = _block_with_dialog(dialog)
        plain = block.format_ask_user_dialog().plain
        assert "[✔]" in plain
        assert "[ ]" in plain
        assert OTHER_DISPLAY in plain
        assert NEXT_LABEL in plain

    def test_single_select_has_no_checkboxes_or_next(self):
        block = _block_with_dialog(AskUserDialog([_q(multi=False)]))
        plain = block.format_ask_user_dialog().plain
        assert "[ ]" not in plain
        assert NEXT_LABEL not in plain

    def test_preview_renders_for_active_option(self):
        block = _block_with_dialog(AskUserDialog([_q(previews=True)]))
        plain = block.format_ask_user_dialog().plain
        assert "preview 0" in plain

    def test_draft_shown_in_other_row_while_browsing(self):
        dialog = AskUserDialog([_q()])
        dialog.current_state().draft = "half typed"
        block = _block_with_dialog(dialog)
        plain = block.format_ask_user_dialog().plain
        assert "half typed" in plain


class TestTabBarAndSubmitTab:
    def test_tab_bar_lists_headers_and_submit(self):
        dialog = AskUserDialog([_q(header="One"), _q(header="Two", multi=True)])
        block = _block_with_dialog(dialog)
        plain = block.format_ask_user_dialog().plain
        assert "←" in plain and "→" in plain
        assert "□ One" in plain or "■ One" in plain
        assert "✓ Submit" in plain

    def test_answered_tab_shows_filled_box(self):
        dialog = AskUserDialog([_q(header="One"), _q(header="Two")])
        dialog.current_state().selected = 0
        block = _block_with_dialog(dialog)
        assert "■ One" in block.format_ask_user_dialog().plain

    def test_submit_tab_review_list(self):
        dialog = AskUserDialog([_q(header="Build"), _q(header="Test")])
        dialog.current_state().selected = 1
        dialog.tab = dialog.submit_tab_index
        block = _block_with_dialog(dialog)
        plain = block.format_ask_user_dialog().plain
        assert REVIEW_HEADING in plain
        assert "Build:" in plain
        assert SUBMIT_PICK_LABEL in plain
        assert SUBMIT_PICK_CANCEL in plain

    def test_submit_tab_warning_for_unanswered(self):
        dialog = AskUserDialog([_q(header="Build"), _q(header="Test")])
        dialog.tab = dialog.submit_tab_index
        block = _block_with_dialog(dialog)
        plain = block.format_ask_user_dialog().plain
        assert "⚠ Answer remaining questions before submitting:" in plain
        assert "Build" in plain and "Test" in plain


class TestHintFooter:
    def test_browsing_hint_mentions_navigation(self):
        block = _block_with_dialog(AskUserDialog([_q()]))
        plain = block.format_ask_user_dialog().plain
        assert "↑/↓ to navigate" in plain
        assert "esc to cancel" in plain

    def test_multi_tab_hint_mentions_switching(self):
        # Start on the multi-select tab: the toggle hint belongs to it.
        dialog = AskUserDialog([_q(multi=True), _q(multi=True)])
        dialog.tab = 1
        block = _block_with_dialog(dialog)
        plain = block.format_ask_user_dialog().plain
        assert "tab to switch questions" in plain
        assert "space to toggle" in plain

    def test_collapsed_hint_only(self):
        dialog = AskUserDialog([_q()])
        dialog.collapsed = True
        block = _block_with_dialog(dialog)
        plain = block.format_ask_user_dialog().plain
        assert "ctrl+] to expand" in plain
        assert "esc to cancel" in plain
        assert "option-0" not in plain


class _AskUserFocusApp(App):
    """Minimal app hosting a ChatLog with one tool block and an
    input-box, so we can verify focus moves when inline inputs appear."""

    CSS = get_styles()

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")
        from vtx.tui.input import InputBox

        yield InputBox(id="input-box")


@pytest.mark.asyncio
async def test_custom_input_focuses_when_input_mode_starts():
    async with _AskUserFocusApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        block = chat.start_tool("ask_user", "tool-1")
        dialog = AskUserDialog([_q()])
        chat.show_ask_user("tool-1", dialog=dialog)
        chat_textarea = pilot.app.query_one("#input-box").query_one("#input-textarea")
        chat_textarea.focus()
        await pilot.pause()

        custom_input = block.query_one("#ask-user-input", AskUserInput)
        assert custom_input.display is False
        assert chat_textarea.has_focus

        # Focus the Type something. row (2 options + other = row 2).
        dialog.handle_key("down")
        dialog.handle_key("down")
        dialog.handle_key("enter")
        chat.rerender_ask_user("tool-1")
        await pilot.pause()

        assert custom_input.display is True
        assert custom_input.has_focus
        assert not chat_textarea.has_focus

        # Navigate away: input hides, focus returns to the chat box.
        dialog.handle_key("up", custom_value=custom_input.value)
        chat.rerender_ask_user("tool-1")
        await pilot.pause()

        assert custom_input.display is False
        assert chat_textarea.has_focus
