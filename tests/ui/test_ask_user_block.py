"""Tests for the inline ask_user rendering on the ToolBlock.

These tests don't spin up a Textual app — they exercise the data-shaping
and rendering helpers directly, plus the public show/update/hide API
that the agent runner calls. The end-to-end flow is covered by the
turn-runner tests in ``tests/tools/test_ask_user_turn.py``.
"""

from vtx.permissions import AskUserOption
from vtx.ui.blocks import ASK_USER_OTHER_DISPLAY, ASK_USER_OTHER_LABEL, ToolBlock


def _options(n: int = 2) -> list[AskUserOption]:
    return [AskUserOption(label=f"option-{i}", description=f"desc {i}") for i in range(n)]


class TestAskUserOptionConstants:
    def test_other_label_is_a_sentinel(self):
        # The sentinel must not collide with any plausible user label.
        # Wrapped in NUL bytes so the LLM can't realistically produce
        # the same string.
        assert ASK_USER_OTHER_LABEL.startswith("\x00")
        assert ASK_USER_OTHER_LABEL.endswith("\x00")

    def test_other_display_is_human_readable(self):
        assert "Other" in ASK_USER_OTHER_DISPLAY


class TestToolBlockAskUserState:
    def test_initial_state_is_idle(self):
        block = ToolBlock(name="ask_user", call_msg="question")
        assert block._ask_user_options == []
        assert block._ask_user_toggled == set()
        assert block._ask_user_highlight == 0
        assert block._ask_user_multi is False
        assert block.is_awaiting_ask_user is False
        assert not block.has_class("-ask-user")

    def test_show_ask_user_marks_block(self):
        block = ToolBlock(name="ask_user", call_msg="question")
        block.show_ask_user(options=_options(2), multi_select=False)
        assert block.is_awaiting_ask_user is True
        assert block.has_class("-ask-user")
        assert block._ask_user_options is not _options(2)  # copied
        assert len(block._ask_user_options) == 2
        assert block._ask_user_multi is False
        assert block._ask_user_highlight == 0

    def test_hide_ask_user_clears_state(self):
        block = ToolBlock(name="ask_user", call_msg="question")
        block.show_ask_user(options=_options(2), multi_select=True)
        block._ask_user_toggled.add("option-0")
        block.hide_ask_user()
        assert block.is_awaiting_ask_user is False
        assert not block.has_class("-ask-user")
        assert block._ask_user_options == []
        assert block._ask_user_toggled == set()
        assert block._ask_user_highlight == 0
        assert block._ask_user_multi is False


class TestToolBlockAskUserHighlight:
    def test_move_wraps(self):
        block = ToolBlock(name="ask_user", call_msg="q")
        block.show_ask_user(options=_options(2), multi_select=False)
        # total rows = 2 user options + 1 Other = 3
        assert block._ask_user_highlight == 0
        block.update_ask_user_selection(highlight=1)
        assert block._ask_user_highlight == 1
        block.update_ask_user_selection(highlight=2)
        assert block._ask_user_highlight == 2  # Other
        block.update_ask_user_selection(highlight=3)
        # update_ask_user_selection clamps rather than wrapping; the
        # app layer is responsible for wrapping.
        assert block._ask_user_highlight == 2

    def test_move_clamps_under_update(self):
        block = ToolBlock(name="ask_user", call_msg="q")
        block.show_ask_user(options=_options(2), multi_select=False)
        block.update_ask_user_selection(highlight=99)
        # Index past the last row is clamped.
        assert block._ask_user_highlight == 2  # clamped to Other

    def test_move_clamps_negative(self):
        block = ToolBlock(name="ask_user", call_msg="q")
        block.show_ask_user(options=_options(2), multi_select=False)
        block.update_ask_user_selection(highlight=-5)
        assert block._ask_user_highlight == 0

    def test_update_can_replace_toggled_set(self):
        block = ToolBlock(name="ask_user", call_msg="q")
        block.show_ask_user(options=_options(3), multi_select=True)
        block.update_ask_user_selection(highlight=0, toggled={"option-0", "option-2"})
        assert block._ask_user_toggled == {"option-0", "option-2"}

    def test_update_keeps_toggled_when_omitted(self):
        block = ToolBlock(name="ask_user", call_msg="q")
        block.show_ask_user(options=_options(2), multi_select=True)
        block._ask_user_toggled.add("option-0")
        block.update_ask_user_selection(highlight=1)
        assert "option-0" in block._ask_user_toggled


class TestToolBlockAskUserToggle:
    """The block stores toggled state; the app's key handler is what
    mutates it via ``update_ask_user_selection(toggled=...)``.
    """

    def test_toggled_state_starts_empty(self):
        block = ToolBlock(name="ask_user", call_msg="q")
        block.show_ask_user(options=_options(2), multi_select=True)
        assert block._ask_user_toggled == set()

    def test_update_sets_toggled_state(self):
        block = ToolBlock(name="ask_user", call_msg="q")
        block.show_ask_user(options=_options(3), multi_select=True)
        block.update_ask_user_selection(highlight=1, toggled={"option-1"})
        assert block._ask_user_toggled == {"option-1"}

    def test_update_can_toggle_multiple(self):
        block = ToolBlock(name="ask_user", call_msg="q")
        block.show_ask_user(options=_options(3), multi_select=True)
        block.update_ask_user_selection(highlight=0, toggled={"option-0"})
        block.update_ask_user_selection(highlight=1, toggled={"option-0", "option-1"})
        assert block._ask_user_toggled == {"option-0", "option-1"}


class TestToolBlockAskUserRendering:
    """The format helpers return Rich Text — exercise them without
    composing the widget into a real app.
    """

    def test_options_includes_other_row(self):
        block = ToolBlock(name="ask_user", call_msg="q")
        block.show_ask_user(options=_options(2), multi_select=False)
        text = block._format_ask_user_options()
        plain = text.plain
        # Both user options appear
        assert "option-0" in plain
        assert "option-1" in plain
        # And the synthetic "Other" row
        assert "Other" in plain

    def test_highlighted_row_includes_number_chip(self):
        block = ToolBlock(name="ask_user", call_msg="q")
        block.show_ask_user(options=_options(3), multi_select=False)
        block._ask_user_highlight = 1
        text = block._format_ask_user_options()
        plain = text.plain
        # Each row is prefixed with [1], [2], [3]
        assert "[1]" in plain
        assert "[2]" in plain
        assert "[3]" in plain

    def test_multi_select_shows_toggle_markers(self):
        block = ToolBlock(name="ask_user", call_msg="q")
        block.show_ask_user(options=_options(2), multi_select=True)
        block._ask_user_toggled.add("option-0")
        text = block._format_ask_user_options()
        plain = text.plain
        # Checkmark for toggled, circle for un-toggled
        assert "✓" in plain
        assert "○" in plain

    def test_single_select_hides_toggle_markers(self):
        block = ToolBlock(name="ask_user", call_msg="q")
        block.show_ask_user(options=_options(2), multi_select=False)
        text = block._format_ask_user_options()
        plain = text.plain
        assert "✓" not in plain
        assert "○" not in plain

    def test_hint_includes_total_count(self):
        block = ToolBlock(name="ask_user", call_msg="q")
        block.show_ask_user(options=_options(3), multi_select=False)
        hint = block._format_ask_user_hint()
        # 3 options + 1 Other = 4
        assert "4" in hint.plain

    def test_multi_select_hint_mentions_space(self):
        block = ToolBlock(name="ask_user", call_msg="q")
        block.show_ask_user(options=_options(2), multi_select=True)
        hint = block._format_ask_user_hint()
        assert "space" in hint.plain

    def test_single_select_hint_mentions_arrows(self):
        block = ToolBlock(name="ask_user", call_msg="q")
        block.show_ask_user(options=_options(2), multi_select=False)
        hint = block._format_ask_user_hint()
        assert "↑↓" in hint.plain
        assert "enter" in hint.plain
