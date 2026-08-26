from collections import deque
from typing import Any, cast

from vtx.core.types import ImageContent
from vtx.tui.queue_ui import QueueUIMixin


class FakeQueueDisplay:
    def __init__(self) -> None:
        self.items: list[tuple[str, bool]] = []
        self.selected: int | None = None
        self.editing: int | None = None

    def update_items(
        self,
        items: list[tuple[str, bool]],
        selected: int | None = None,
        editing: int | None = None,
    ) -> None:
        self.items = items
        self.selected = selected
        self.editing = editing


class FakeInputBox:
    def __init__(self) -> None:
        self.text = ""
        self.focused = False

    def clear(self, *, reset_pastes: bool = True) -> None:
        del reset_pastes
        self.text = ""

    def insert(self, text: str) -> None:
        self.text += text

    def focus(self) -> None:
        self.focused = True


class FakeVtx:
    def __init__(self) -> None:
        self._pending_queue: deque[tuple[str, str, list[ImageContent] | None]] = deque()
        self._steer_queue: deque[tuple[str, str, list[ImageContent] | None]] = deque()
        self._queue_selection: tuple[bool, int] | None = None
        self._queue_editing: (
            tuple[bool, int, tuple[str, str, list[ImageContent] | None]] | None
        ) = None
        self.queue_display = FakeQueueDisplay()
        self.input_box = FakeInputBox()

    def query_one(self, selector: str, *_args: Any, **_kwargs: Any) -> object:
        if selector == "#queue-display":
            return self.queue_display
        if selector == "#input-box":
            return self.input_box
        raise AssertionError(f"Unexpected selector: {selector}")

    def _queue_items(self) -> list[tuple[bool, int, str, str]]:
        return QueueUIMixin._queue_items(cast(QueueUIMixin, self))

    def _selected_queue_flat_index(self) -> int | None:
        return QueueUIMixin._selected_queue_flat_index(cast(QueueUIMixin, self))

    def _set_queue_selection_by_flat_index(self, flat_index: int | None) -> None:
        QueueUIMixin._set_queue_selection_by_flat_index(cast(QueueUIMixin, self), flat_index)

    def _update_queue_display(self) -> None:
        QueueUIMixin._update_queue_display(cast(QueueUIMixin, self))

    def start_queue_edit(self) -> bool:
        return QueueUIMixin.start_queue_edit(cast(QueueUIMixin, self))

    def finish_queue_edit(self, display_text: str, query_text: str) -> bool:
        return QueueUIMixin.finish_queue_edit(cast(QueueUIMixin, self), display_text, query_text)

    def cancel_queue_edit(self) -> bool:
        return QueueUIMixin.cancel_queue_edit(cast(QueueUIMixin, self))

    def delete_selected_queue_item(self) -> bool:
        return QueueUIMixin.delete_selected_queue_item(cast(QueueUIMixin, self))


def _queue(*items: str) -> deque[tuple[str, str, list[ImageContent] | None]]:
    return deque((f"display {item}", f"query {item}", None) for item in items)


def test_editing_last_queue_item_keeps_visible_editing_placeholder() -> None:
    app = FakeVtx()
    app._pending_queue = _queue("one", "two", "three")
    app._queue_selection = (False, 2)

    assert app.start_queue_edit() is True

    assert list(app._pending_queue) == [
        ("display one", "query one", None),
        ("display two", "query two", None),
    ]
    assert app.input_box.text == "query three"
    assert app.input_box.focused is True
    assert app.queue_display.items == [
        ("display one", False),
        ("display two", False),
        ("display three", False),
    ]
    assert app.queue_display.selected == 2
    assert app.queue_display.editing == 2


def test_finish_queue_edit_restores_updated_item_at_original_position() -> None:
    app = FakeVtx()
    app._pending_queue = _queue("one", "two", "three")
    app._queue_selection = (False, 2)
    app.start_queue_edit()

    assert app.finish_queue_edit("display updated", "query updated") is True

    assert list(app._pending_queue) == [
        ("display one", "query one", None),
        ("display two", "query two", None),
        ("display updated", "query updated", None),
    ]
    assert app._queue_editing is None
    assert app._queue_selection == (False, 2)
    assert app.queue_display.items[-1] == ("display updated", False)
    assert app.queue_display.editing is None


def test_cancel_queue_edit_restores_original_item_and_clears_editor() -> None:
    app = FakeVtx()
    app._pending_queue = _queue("one", "two", "three")
    app._queue_selection = (False, 1)
    app.start_queue_edit()
    app.input_box.text = "edited draft"

    assert app.cancel_queue_edit() is True

    assert list(app._pending_queue) == [
        ("display one", "query one", None),
        ("display two", "query two", None),
        ("display three", "query three", None),
    ]
    assert app.input_box.text == ""
    assert app._queue_editing is None
    assert app._queue_selection == (False, 1)


def test_ctrl_d_delete_removes_selected_queue_item() -> None:
    app = FakeVtx()
    app._pending_queue = _queue("one", "two", "three")
    app._queue_selection = (False, 1)

    assert app.delete_selected_queue_item() is True

    assert list(app._pending_queue) == [
        ("display one", "query one", None),
        ("display three", "query three", None),
    ]
    assert app.queue_display.items == [("display one", False), ("display three", False)]
    assert app.queue_display.selected == 1
