from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from vtx.runtime import ConversationRuntime
from vtx.session import SessionInfo
from vtx.ui.app import Vtx
from vtx.ui.autocomplete import (
    FilePathProvider,
    PullRequestProvider,
    SlashCommand,
    SlashCommandProvider,
)
from vtx.ui.floating_list import FloatingList, ListItem
from vtx.ui.input import InputBox
from vtx.ui.selection_mode import SelectionMode


class FakeChat:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.max_scroll_y = 0
        self.scroll_y = 0
        self.scrolled_to_end = False

    def add_info_message(self, message: str, error: bool = False, warning: bool = False) -> None:
        if error:
            self.errors.append(message)

    def scroll_end(self, animate: bool = False) -> None:
        del animate
        self.scrolled_to_end = True


class FakeInfoBar:
    def __init__(self) -> None:
        self.classes: set[str] = set()
        self.removed_classes: list[str] = []

    def add_class(self, class_name: str) -> None:
        self.classes.add(class_name)

    def remove_class(self, class_name: str) -> None:
        self.removed_classes.append(class_name)
        self.classes.discard(class_name)

    def set_permission_mode(self, mode: str) -> None:
        pass


class FakeCompletionList:
    def __init__(
        self, selected_item: ListItem | None = None, *, visible: bool | None = None
    ) -> None:
        self.items: list[ListItem] = []
        self.selected_item = selected_item
        self.searchable: bool | None = None
        self.max_label_width: int | None = None
        initially_visible = selected_item is not None if visible is None else visible
        self.hidden = not initially_visible
        self.updated_items: list[ListItem] | None = None

    @property
    def is_visible(self) -> bool:
        return not self.hidden

    def show(
        self, items: list[ListItem], searchable: bool = False, max_label_width: int | None = None
    ) -> None:
        self.items = items
        self.searchable = searchable
        self.max_label_width = max_label_width
        self.hidden = False
        self.selected_item = items[0] if items else None

    def update_items(self, items: list[ListItem]) -> None:
        self.updated_items = items
        self.items = items

    def hide(self) -> None:
        self.hidden = True

    def select_value(self, value: object) -> None:
        for item in self.items:
            if item.value == value:
                self.selected_item = item
                return


class FakeInputBox:
    def __init__(self) -> None:
        self.is_tab_completing = False
        self.active_provider: (
            SlashCommandProvider | FilePathProvider | PullRequestProvider | None
        ) = None
        self.completing: bool | None = None
        self.submitted = False
        self.tab_completed_items: list[ListItem] = []
        self.slash_completed_items: list[ListItem] = []
        self.file_completed_items: list[ListItem] = []
        self.provider_completed_items: list[ListItem] = []
        self.cleared = False
        self.autocomplete_enabled: bool | None = None

    def set_completing(self, completing: bool) -> None:
        self.completing = completing

    def submit_raw(self) -> None:
        self.submitted = True

    def apply_tab_path_completion(self, item: ListItem) -> None:
        self.tab_completed_items.append(item)

    def apply_slash_command(self, item: ListItem) -> None:
        self.slash_completed_items.append(item)

    def apply_file_completion(self, item: ListItem) -> None:
        self.file_completed_items.append(item)

    def apply_provider_completion(self, item: ListItem) -> None:
        self.provider_completed_items.append(item)

    def clear(self) -> None:
        self.cleared = True

    def set_autocomplete_enabled(self, enabled: bool) -> None:
        self.autocomplete_enabled = enabled

    def focus(self) -> None:
        pass


class FakeVtx:
    def __init__(
        self, selected_item: ListItem | None = None, *, completion_visible: bool | None = None
    ) -> None:
        self.chat = FakeChat()
        self.info_bar = FakeInfoBar()
        self.completion_list = FakeCompletionList(selected_item, visible=completion_visible)
        self.input_box = FakeInputBox()
        self._runtime = ConversationRuntime(
            cwd=".",
            model="fake-model",
            model_provider="fake",
            api_key=None,
            base_url=None,
            thinking_level="off",
            tools=[],
        )
        self._selection_mode: SelectionMode | None = None
        self._settings_active = False
        self._settings_selected_value: str | None = None
        self.reset_delete_count = 0
        self.selected_permissions: list[str] = []
        self.selected_themes: list[str] = []
        self.resume_items: list[ListItem] = []
        self.notifications: list[tuple[str, str, int, str]] = []

    def query_one(self, selector: str, widget_type: object | None = None) -> Any:
        if selector == "#chat-log":
            return self.chat
        if selector == "#info-bar":
            return self.info_bar
        if selector == "#completion-list":
            return self.completion_list
        if selector == "#input-box":
            return self.input_box
        raise AssertionError(f"Unexpected selector: {selector}")

    @contextmanager
    def batch_update(self):
        yield

    def _reset_ctrl_d_delete_state(self) -> None:
        self.reset_delete_count += 1

    def _select_permission_mode(self, mode: str) -> None:
        self.selected_permissions.append(mode)

    def _select_theme(self, theme_id: str) -> None:
        self.selected_themes.append(theme_id)

    def _vtx(self) -> Vtx:
        return cast(Vtx, self)

    def call_after_refresh(self, callback: Callable[[], None]) -> None:
        callback()

    def call_later(self, callback: Callable[..., None], *args: Any) -> None:
        callback(*args)

    def _is_chat_at_bottom(self) -> bool:
        return Vtx._is_chat_at_bottom(self._vtx())

    def _restore_chat_scroll_if_needed(self, was_at_bottom: bool) -> None:
        Vtx._restore_chat_scroll_if_needed(self._vtx(), was_at_bottom)

    def _restore_chat_scroll_after_refresh(self, was_at_bottom: bool) -> None:
        Vtx._restore_chat_scroll_after_refresh(self._vtx(), was_at_bottom)

    def _set_bottom_info_displaced(self, displaced: bool) -> None:
        Vtx._set_bottom_info_displaced(self._vtx(), displaced)

    def _show_completion_list(
        self,
        items: list[ListItem],
        *,
        searchable: bool = False,
        max_label_width: int | None = None,
    ) -> None:
        Vtx._show_completion_list(
            self._vtx(), items, searchable=searchable, max_label_width=max_label_width
        )

    def _hide_completion_list(self, *, restore_info_bar: bool = True) -> None:
        Vtx._hide_completion_list(self._vtx(), restore_info_bar=restore_info_bar)

    def on_completion_update(self, event: InputBox.CompletionUpdate) -> None:
        Vtx.on_completion_update(self._vtx(), event)

    def on_completion_hide(self, event: InputBox.CompletionHide) -> None:
        Vtx.on_completion_hide(self._vtx(), event)

    def on_completion_select(self, event: InputBox.CompletionSelect) -> None:
        Vtx.on_completion_select(self._vtx(), event)

    def _apply_selection_mode_choice(
        self, item: ListItem, input_box: Any, was_at_bottom: bool
    ) -> None:
        Vtx._apply_selection_mode_choice(self._vtx(), item, input_box, was_at_bottom)

    def _show_selection_picker(
        self,
        items: list[ListItem],
        selection_mode: SelectionMode,
        *,
        searchable: bool = True,
        max_label_width: int | None = None,
    ) -> None:
        Vtx._show_selection_picker(
            self._vtx(),
            items,
            selection_mode,
            searchable=searchable,
            max_label_width=max_label_width,
        )

    def _handle_settings_select(self, item_value: str):
        return Vtx._handle_settings_select(self._vtx(), item_value)

    def _build_settings_items(self) -> list[ListItem[str]]:
        return Vtx._build_settings_items(self._vtx())

    def _show_settings_picker(self, selected_value: str | None = None) -> None:
        Vtx._show_settings_picker(self._vtx(), selected_value=selected_value)

    def _build_resume_items(self) -> list[ListItem]:
        return self.resume_items

    def _delete_selected_resume_session(self) -> None:
        Vtx._delete_selected_resume_session(self._vtx())

    def notify(
        self, message: str, *, title: str = "", timeout: int = 0, severity: str = "information"
    ) -> None:
        self.notifications.append((message, title, timeout, severity))

    def _handle_themes_command(self, args: str) -> None:
        Vtx._handle_themes_command(self._vtx(), args)

    def _handle_thinking_command(self, args: str) -> None:
        Vtx._handle_thinking_command(self._vtx(), args)


def _make_session_item(path, session_id: str = "session") -> ListItem:
    session_info = SessionInfo(
        id=session_id,
        path=path,
        cwd=".",
        created=datetime.now(UTC),
        modified=datetime.now(UTC),
        message_count=1,
        first_message=session_id,
    )
    return ListItem(value=session_info, label=session_id)


def test_completion_list_is_configured_for_ten_rows() -> None:
    app = Vtx(cwd=".")
    floating_list = next(
        widget
        for widget in app.compose()
        if isinstance(widget, FloatingList) and widget.id == "completion-list"
    )

    assert floating_list._window_size == 10


def test_show_completion_list_displaces_info_bar() -> None:
    app = FakeVtx()
    items = [ListItem(value="one", label="one")]

    app._show_completion_list(items, searchable=True, max_label_width=40)

    assert "-completion-hidden" in app.info_bar.classes
    assert app.completion_list.items == items
    assert app.completion_list.searchable is True
    assert app.completion_list.max_label_width == 40


def test_show_selection_picker_displaces_info_bar_and_restores_scroll() -> None:
    app = FakeVtx()
    item = ListItem(value="one", label="one")

    app._show_selection_picker([item], SelectionMode.PERMISSIONS)

    assert "-completion-hidden" in app.info_bar.classes
    assert app.completion_list.hidden is False
    assert app.completion_list.items == [item]
    assert app._selection_mode == SelectionMode.PERMISSIONS
    assert app.chat.scrolled_to_end is True


def test_completion_update_displaces_info_bar_for_visible_list() -> None:
    app = FakeVtx(completion_visible=True)
    items = [ListItem(value="one", label="one")]

    app.on_completion_update(InputBox.CompletionUpdate(items))

    assert "-completion-hidden" in app.info_bar.classes
    assert app.completion_list.updated_items == items
    assert app.chat.scrolled_to_end is True


@pytest.mark.parametrize(
    ("restore_info_bar", "expected_hidden_class"), [(True, False), (False, True)]
)
def test_hide_completion_list_controls_info_bar_restore(
    restore_info_bar: bool, expected_hidden_class: bool
) -> None:
    app = FakeVtx()
    app.info_bar.classes.add("-completion-hidden")

    app._hide_completion_list(restore_info_bar=restore_info_bar)

    assert app.completion_list.hidden is True
    assert ("-completion-hidden" in app.info_bar.classes) is expected_hidden_class


@pytest.mark.parametrize("case", ["no-item", "tab", "slash", "file"])
def test_completion_select_terminal_paths_restore_info_bar(case: str) -> None:
    item = None if case == "no-item" else ListItem(value="value", label="value")
    app = FakeVtx(selected_item=item, completion_visible=True)
    app.info_bar.classes.add("-completion-hidden")

    if case == "tab":
        app.input_box.is_tab_completing = True
    elif case == "slash":
        app.input_box.active_provider = SlashCommandProvider(
            [SlashCommand(name="help", description="help")]
        )
    elif case == "file":
        app.input_box.active_provider = FilePathProvider(".")

    app.on_completion_select(InputBox.CompletionSelect())

    assert app.completion_list.hidden is True
    assert "-completion-hidden" not in app.info_bar.classes
    assert app.chat.scrolled_to_end is True

    if case == "no-item":
        assert app.input_box.completing is False
        assert app.input_box.submitted is True
    elif case == "tab":
        assert app.input_box.tab_completed_items == [item]
    elif case == "slash":
        assert app.input_box.slash_completed_items == [item]
        assert app.input_box.completing is False
    elif case == "file":
        assert app.input_box.provider_completed_items == [item]
        assert app.input_box.completing is False


def test_completion_select_final_selection_mode_restores_info_bar() -> None:
    app = FakeVtx(selected_item=ListItem(value="auto", label="auto"))
    app.info_bar.classes.add("-completion-hidden")
    app._selection_mode = SelectionMode.PERMISSIONS

    app.on_completion_select(InputBox.CompletionSelect())

    assert app.completion_list.hidden is True
    assert "-completion-hidden" not in app.info_bar.classes
    assert app._selection_mode is None
    assert app.input_box.cleared is True
    assert app.input_box.autocomplete_enabled is True
    assert app.input_box.completing is False
    assert app.selected_permissions == ["auto"]
    assert app.chat.scrolled_to_end is True


def test_completion_select_settings_themes_keeps_info_bar_displaced() -> None:
    app = FakeVtx(selected_item=ListItem(value="themes", label="themes"))
    app.info_bar.classes.add("-completion-hidden")
    app._selection_mode = SelectionMode.SETTINGS

    app.on_completion_select(InputBox.CompletionSelect())

    assert "-completion-hidden" not in app.info_bar.removed_classes
    assert "-completion-hidden" in app.info_bar.classes
    assert app.completion_list.hidden is False
    assert app._selection_mode == SelectionMode.THEME
    assert app._settings_active is True
    assert app.chat.scrolled_to_end is True


def test_completion_select_settings_thinking_without_provider_restores_info_bar() -> None:
    app = FakeVtx(selected_item=ListItem(value="thinking", label="thinking"))
    app.info_bar.classes.add("-completion-hidden")
    app._selection_mode = SelectionMode.SETTINGS

    app.on_completion_select(InputBox.CompletionSelect())

    assert app.completion_list.hidden is True
    assert "-completion-hidden" not in app.info_bar.classes
    assert app._selection_mode is None
    assert app._settings_active is False
    assert app.chat.errors == ["Agent not initialized"]
    assert app.chat.scrolled_to_end is True


def test_completion_hide_from_settings_subpicker_reopens_settings_and_restores_scroll() -> None:
    app = FakeVtx(completion_visible=True)
    app.info_bar.classes.add("-completion-hidden")
    app._selection_mode = SelectionMode.THEME
    app._settings_active = True

    app.on_completion_hide(InputBox.CompletionHide())

    assert app.completion_list.hidden is False
    assert "-completion-hidden" in app.info_bar.classes
    assert app._selection_mode == SelectionMode.SETTINGS
    assert app._settings_active is False
    assert app.chat.scrolled_to_end is True


def test_resume_delete_no_remaining_sessions_hides_picker_and_restores_scroll(tmp_path) -> None:
    session_path = tmp_path / "deleted.jsonl"
    session_path.write_text("{}\n")
    app = FakeVtx(selected_item=_make_session_item(session_path))
    app.info_bar.classes.add("-completion-hidden")
    app._selection_mode = SelectionMode.SESSION

    app._delete_selected_resume_session()

    assert session_path.exists() is False
    assert app.completion_list.hidden is True
    assert "-completion-hidden" not in app.info_bar.classes
    assert app.input_box.autocomplete_enabled is True
    assert app.input_box.completing is False
    assert app._selection_mode is None
    assert app.notifications == [
        ("Session deleted (no saved sessions left)", "Sessions", 2, "information")
    ]
    assert app.chat.scrolled_to_end is True


def test_resume_delete_remaining_sessions_updates_picker_and_restores_scroll(tmp_path) -> None:
    deleted_path = tmp_path / "deleted.jsonl"
    remaining_path = tmp_path / "remaining.jsonl"
    deleted_path.write_text("{}\n")
    remaining_path.write_text("{}\n")
    remaining_item = _make_session_item(remaining_path, "remaining")
    app = FakeVtx(selected_item=_make_session_item(deleted_path, "deleted"))
    app._selection_mode = SelectionMode.SESSION
    app.resume_items = [remaining_item]

    app._delete_selected_resume_session()

    assert deleted_path.exists() is False
    assert app.completion_list.hidden is False
    assert app.completion_list.updated_items == [remaining_item]
    assert app.notifications == [("Session deleted", "Sessions", 2, "information")]
    assert app.chat.scrolled_to_end is True


def test_completion_select_settings_subpicker_reopens_settings_and_restores_scroll() -> None:
    app = FakeVtx(selected_item=ListItem(value="textual-dark", label="textual-dark"))
    app.info_bar.classes.add("-completion-hidden")
    app._selection_mode = SelectionMode.THEME
    app._settings_active = True

    app.on_completion_select(InputBox.CompletionSelect())

    assert app.selected_themes == ["textual-dark"]
    assert app.completion_list.hidden is False
    assert "-completion-hidden" in app.info_bar.classes
    assert app._selection_mode == SelectionMode.SETTINGS
    assert app._settings_active is False
    assert app.chat.scrolled_to_end is True
