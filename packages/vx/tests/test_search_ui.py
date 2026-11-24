"""The shell's search UI and OSC 8 link hit-testing."""

import asyncio
from dataclasses import dataclass

from vx.app import VxApp
from vx.tui import ansi, blocks


@dataclass
class StubBlock(blocks.Block):
    kind: str = "assistant"
    text: str = "x"

    def rows(self, width: int) -> list[str]:
        return [self.text]


class StubTerminal:
    _fd = -1

    def __init__(self) -> None:
        self.writes: list[str] = []

    def size(self) -> tuple[int, int]:
        return 24, 80

    def write(self, data: str) -> None:
        self.writes.append(data)

    def enter(self) -> None:
        pass

    def exit(self) -> None:
        pass


def make_app() -> VxApp:
    app = VxApp(cwd="/tmp", terminal=StubTerminal())
    app._ready = asyncio.Event()
    app._ready.set()
    app._draw = lambda *args, **kwargs: None
    return app


def test_ctrl_f_opens_incremental_search():
    app = make_app()
    app._handle_key("ctrl+f")
    assert app._searching

    app._handle_key("h")
    app._handle_key("i")
    assert app._search == "hi"
    assert app.viewport.search == "hi"

    app._handle_key("esc")
    assert not app._searching
    assert app.viewport.search == "hi"


def test_search_footer_shows_query_and_count():
    app = make_app()
    app._handle_key("ctrl+f")
    app._handle_key("x")
    rows, caret = app._footer(80, 24)
    assert rows[0].startswith("\x1b[")
    assert any("⌕ " in row for row in rows)
    assert any("no matches" in row for row in rows)
    assert caret is not None


def test_navigation_keys_still_scroll_while_searching():
    app = make_app()
    for i in range(40):
        app.viewport.add(StubBlock(text=f"row{i}"))
    app._handle_key("ctrl+f")
    app._handle_key("pageup")
    assert app.viewport.scroll > 0


def test_ctrl_end_jumps_to_latest_and_clears_unseen():
    app = make_app()
    for i in range(40):
        app.viewport.add(StubBlock(text=f"row{i}"))
    app.viewport.visible(80, 24)
    app.viewport.scroll_by(5, 80, 24)
    app.viewport.add(StubBlock(text="late"))
    app.viewport.visible(80, 24)
    assert app.viewport.unseen > 0

    app._handle_key("ctrl+end")
    assert app.viewport.scroll == 0
    assert app.viewport.follow
    assert app.viewport.unseen == 0


def test_pill_is_painted_when_behind_the_tail():
    app = VxApp(cwd="/tmp", terminal=StubTerminal())
    app._ready = asyncio.Event()
    app._ready.set()
    for i in range(40):
        app.viewport.add(StubBlock(text=f"row{i}"))
    app.viewport.visible(80, 24)
    app.viewport.scroll_by(5, 80, 24)
    app.viewport.add(StubBlock(text="late"))
    app.viewport.visible(80, 24)

    app._draw()
    assert "Jump to latest message Ctrl+End" in "".join(app.term.writes)


def test_click_opens_link_under_pointer():
    app = make_app()
    opened: list[str] = []
    app._open_link = lambda uri: opened.append(uri)
    app._frame = ["", ansi.osc8("https://example.com", "docs")]
    app._mouse_key("mouse:left:3:2")
    assert opened == ["https://example.com"]
