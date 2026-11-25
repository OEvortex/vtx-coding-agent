"""The sidebar: two sections, real data, and the frame that holds it."""

import asyncio
from typing import cast

import pytest
from ctx.core.events import ToolResultEvent, ToolStartEvent
from ctx.protocol.types import FileChanges, TextContent, ToolResultMessage
from vx import prefs
from vx.app import VxApp
from vx.tui import ansi, blocks, footer
from vx.tui.sidebar import Sidebar, Todo, Touch, sidebar_cols
from vx.tui.terminal import Terminal


class StubTerminal:
    _fd = -1

    def __init__(self, cols: int = 100, rows: int = 26) -> None:
        self.cols, self.rows = cols, rows

    def size(self) -> tuple[int, int]:
        return self.rows, self.cols

    def write(self, data: str) -> None:
        pass

    def enter(self) -> None:
        pass

    def exit(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _own_prefs(tmp_path, monkeypatch):
    """Every test here gets its own prefs file; the real one is not ours."""
    monkeypatch.setattr(prefs, "prefs_path", lambda: tmp_path / "vx-prefs.json")


def plain(rows: list[str]) -> list[str]:
    return [ansi.strip_ansi(row).rstrip() for row in rows]


def make_app(cols: int = 100, rows: int = 26) -> VxApp:
    app = VxApp(cwd="/tmp", terminal=cast(Terminal, StubTerminal(cols, rows)))
    app._ready = asyncio.Event()
    app._ready.set()
    return app


def give_plan(app: VxApp, *todos: Todo) -> VxApp:
    """Put work in the panel and let it follow, the way a tool result does."""
    app._sidebar.todos = list(todos)
    app._sync_sidebar()
    return app


def give_files(app: VxApp, *touches: Touch) -> VxApp:
    app._sidebar.touches = list(touches)
    app._sync_sidebar()
    return app


def has_panel(app: VxApp) -> bool:
    app._draw(force=True)
    return any("Tasks" in row for row in plain(app._frame))


# -- layout ----------------------------------------------------------------


def test_the_panel_never_takes_more_than_a_quarter_of_the_width():
    assert sidebar_cols(132, 34) == 30
    assert sidebar_cols(100, 26) == 25
    assert sidebar_cols(240, 40) == 30


def test_a_small_terminal_gets_no_panel_at_all():
    # A canvas narrower than a diff hunk, or shorter than both sections.
    assert sidebar_cols(90, 26) == 0
    assert sidebar_cols(100, 16) == 0
    assert sidebar_cols(92, 20) == sidebar_cols(92, 20) > 0


def test_the_panel_fills_exactly_the_height_it_is_given():
    panel = Sidebar([Todo("read it", "done")], [Touch("a.py", 1, 1)])
    for height in (3, 4, 7, 12, 30):
        assert len(panel.rows(30, height)) == height
    assert len(Sidebar().rows(30, 5)) == 5


def test_no_row_is_wider_than_the_panel():
    todos = [Todo("a step " * 12, "active"), Todo("another step", "done")]
    touches = [Touch("some/very/long/path/name" * 3 + ".py", 12, 3)]
    for width in (12, 18, 24, 30, 61):
        for height in range(3, 40):
            for todos_open in (True, False):
                for files_open in (True, False):
                    panel = Sidebar(todos, touches, todos_open, files_open)
                    for row in panel.rows(width, height):
                        assert ansi.width(row) <= max(width, 0), (width, height, row)


def test_the_plan_shows_status_glyphs_and_a_count():
    panel = Sidebar([Todo("read", "done"), Todo("write", "active"), Todo("test")])
    text = "\n".join(plain(panel.rows(30, 12)))
    assert "1/3" in text
    assert "✓ read" in text
    assert "▸ write" in text
    assert "○ test" in text


def test_a_folded_plan_keeps_the_active_step():
    # Folded, the section still answers "what is it doing now".
    panel = Sidebar([Todo("read", "done"), Todo("write the panel", "active")], todos_open=False)
    rows = plain(panel.rows(30, 8))
    assert "Tasks ▸" in rows[0]
    assert "write the panel" in rows[2]
    assert "read" not in "\n".join(rows)


def test_the_file_list_says_what_the_turn_changed():
    panel = Sidebar(touches=[Touch("a/b/app.py", 12, 3), Touch("x/sidebar.py", 40, 0)])
    text = "\n".join(plain(panel.rows(30, 14)))
    assert "Files ▾" in text
    assert "+52 −3" in text  # the section total
    assert "app.py" in text
    assert "+12 −3" in text
    assert "sidebar.py" in text


def test_a_file_edited_twice_is_one_row():
    app = make_app()
    app._bump_touch(FileChanges(path="a.py", added=3, removed=1))
    app._bump_touch(FileChanges(path="b.py", added=1, removed=0))
    app._bump_touch(FileChanges(path="a.py", added=2, removed=4))
    got = [(touch.path, touch.added, touch.removed) for touch in app._sidebar.touches]
    assert got == [("b.py", 1, 0), ("a.py", 5, 5)]


def test_a_new_prompt_starts_the_file_list_over():
    app = make_app()
    app._bump_touch(FileChanges(path="a.py", added=3, removed=1))
    assert app._sidebar.touches
    app._submit()
    assert app._sidebar.touches == []


# -- the frame -------------------------------------------------------------


def test_the_panel_sits_beside_the_transcript_and_the_footer_spans_both():
    app = make_app(100, 26)
    app.viewport.add(blocks.Assistant(text="hello"))
    give_plan(app, Todo("read", "active"))
    app._draw(force=True)
    frame = app._frame
    assert len(frame) == 26
    assert all(ansi.width(row) <= 100 for row in frame)
    assert "Tasks" in frame[0]
    # The rule is one column past the transcript, on every body row.
    for row in frame[:20]:
        assert ansi.strip_ansi(row)[73] == "│"
    # The footer is the terminal's width, not the transcript's.
    assert ansi.width(frame[-1]) == 100


def test_ctrl_t_puts_the_panel_away_and_brings_it_back():
    app = give_plan(make_app(100, 26), Todo("read", "active"))
    assert has_panel(app)
    app._handle_key("ctrl+t")
    assert not has_panel(app)
    app._handle_key("ctrl+t")
    assert has_panel(app)


def test_ctrl_g_closes_both_sections():
    app = make_app(100, 26)
    app._sidebar.todos = [Todo("read", "active")]
    app._sidebar.touches = [Touch("a.py", 1, 1)]
    app._handle_key("ctrl+g")
    assert not app._sidebar.todos_open and not app._sidebar.files_open
    app._handle_key("ctrl+g")
    assert app._sidebar.todos_open and app._sidebar.files_open


def test_a_small_terminal_hides_the_panel_without_losing_it():
    app = give_plan(make_app(90, 26), Todo("read", "active"))
    assert not has_panel(app)
    app.term = cast(Terminal, StubTerminal(120, 30))
    assert has_panel(app)


def test_a_fresh_session_draws_no_panel():
    app = make_app(100, 26)
    app.viewport.add(blocks.Assistant(text="hello"))
    assert not has_panel(app)
    # and the transcript gets the whole width back
    assert "│" not in ansi.strip_ansi(app._frame[1])


# -- real events -----------------------------------------------------------


def result_event(path: str, added: int, removed: int) -> ToolResultEvent:
    return ToolResultEvent(
        tool_call_id="c1",
        tool_name="apply_patch",
        result=ToolResultMessage(
            tool_call_id="c1",
            tool_name="apply_patch",
            content=[TextContent(text="ok")],
            file_changes=[FileChanges(path=path, added=added, removed=removed)],
        ),
        file_changes=[FileChanges(path=path, added=added, removed=removed)],
    )


def test_a_finished_patch_lands_in_the_panel():
    app = make_app()
    app._handle_event(ToolStartEvent(tool_call_id="c1", tool_name="apply_patch"))
    app._handle_event(result_event("src/app.py", 40, 6))
    assert [(t.path, t.added, t.removed) for t in app._sidebar.touches] == [("src/app.py", 40, 6)]


def test_the_plan_comes_from_the_todo_tool():
    app = make_app()
    app._handle_event(
        ToolResultEvent(
            tool_call_id="t1",
            tool_name="todo",
            result=ToolResultMessage(
                tool_call_id="t1",
                tool_name="todo",
                content=[TextContent(text="Plan")],
                ui_data={
                    "todos": [
                        {"text": "read the code", "status": "done"},
                        {"text": "write it", "status": "active"},
                    ]
                },
            ),
        )
    )
    assert [(t.text, t.status) for t in app._sidebar.todos] == [
        ("read the code", "done"),
        ("write it", "active"),
    ]


def test_a_replayed_session_gets_its_panel_back():
    app = make_app()
    app._render_history_messages(
        [
            ToolResultMessage(
                tool_call_id="t1",
                tool_name="todo",
                content=[TextContent(text="Plan")],
                ui_data={"todos": [{"text": "read", "status": "done"}]},
            ),
            ToolResultMessage(
                tool_call_id="p1",
                tool_name="apply_patch",
                content=[TextContent(text="ok")],
                file_changes=[FileChanges(path="a.py", added=2, removed=1)],
            ),
        ]
    )
    assert [t.text for t in app._sidebar.todos] == ["read"]
    assert [(t.path, t.added) for t in app._sidebar.touches] == [("a.py", 2)]


# -- the panel is a habit --------------------------------------------------


def test_the_panel_follows_the_work_by_default():
    assert prefs.sidebar_pinned() is False
    assert prefs.sidebar_label() == "auto"
    assert make_app(100, 26)._sidebar_auto is True
    assert make_app(100, 26)._show_sidebar is False


def test_a_pinned_panel_is_remembered_across_launches():
    app = make_app(100, 26)
    app._handle_key("ctrl+t")
    assert app._show_sidebar is True
    assert make_app(100, 26)._show_sidebar is True
    assert make_app(100, 26)._sidebar_auto is False
    make_app(100, 26)._handle_key("ctrl+t")
    assert make_app(100, 26)._show_sidebar is False


def test_folded_sections_come_back_folded():
    make_app(100, 26)._handle_key("ctrl+g")
    fresh = make_app(100, 26)
    assert not fresh._sidebar.todos_open
    assert not fresh._sidebar.files_open
    assert fresh._sidebar.rows(30, 10)[0].find("Tasks") >= 0


def test_the_preference_survives_a_corrupt_file():
    prefs.prefs_path().write_text("{not json", encoding="utf-8")
    assert prefs.sidebar_pinned() is False
    app = make_app(100, 26)
    assert app._sidebar_auto is True
    assert app._show_sidebar is False


def test_a_non_boolean_pref_falls_back_to_the_default():
    prefs.prefs_path().write_text('{"sidebar_pinned": "yes"}', encoding="utf-8")
    assert prefs.sidebar_pinned() is False


def test_settings_names_what_is_folded():
    prefs.set_sidebar_folds(True, False)
    assert prefs.sidebar_label() == "auto"
    prefs.set_sidebar_shown(True)
    assert prefs.sidebar_label() == "on · files folded"
    prefs.set_sidebar_shown(False)
    assert prefs.sidebar_label() == "off"


def test_settings_can_put_the_panel_away():
    app = give_plan(make_app(100, 26), Todo("read", "active"))
    app.set_sidebar_shown(False)
    app._draw(force=True)
    assert not any("Tasks" in row for row in plain(app._frame))
    assert prefs.sidebar_shown() is False


def test_the_settings_row_runs_the_same_switch():
    app = give_plan(make_app(100, 26), Todo("read", "active"))
    app._draw(force=True)
    assert any("Tasks" in row for row in plain(app._frame))
    # Slash commands are grafted onto the class on first use; do it here so the
    # test drives the same path /settings does.
    from vx.app import _graft_commands
    from vx.commands import CommandsMixin

    _graft_commands(type(app), CommandsMixin)
    app._handle_settings_select(footer.MenuItem(label="sidebar", value="sidebar"))
    app._draw(force=True)
    assert not any("Tasks" in row for row in plain(app._frame))
    assert prefs.sidebar_shown() is False
    assert "│" not in ansi.strip_ansi(app._frame[0])


# -- the panel belongs to the work -----------------------------------------


def todo_result(*items: dict[str, str]) -> ToolResultEvent:
    return ToolResultEvent(
        tool_call_id="t9",
        tool_name="todo",
        result=ToolResultMessage(
            tool_call_id="t9",
            tool_name="todo",
            content=[TextContent(text="Plan")],
            ui_data={"todos": list(items)},
        ),
    )


def test_a_plan_opens_the_panel():
    app = make_app(100, 26)
    assert not app._show_sidebar
    app._handle_event(todo_result({"text": "read", "status": "active"}))
    assert app._show_sidebar is True
    assert has_panel(app)


def test_a_written_file_opens_the_panel():
    app = make_app(100, 26)
    app._handle_event(ToolStartEvent(tool_call_id="c1", tool_name="apply_patch"))
    app._handle_event(result_event("src/app.py", 40, 6))
    assert app._show_sidebar is True
    assert has_panel(app)


def test_the_panel_closes_when_the_work_is_gone():
    app = give_plan(make_app(100, 26), Todo("read", "active"))
    assert app._show_sidebar is True
    app._handle_event(todo_result())
    app._submit()
    assert app._show_sidebar is False
    assert not has_panel(app)


def test_a_plan_that_stays_keeps_the_panel_open_across_turns():
    app = give_plan(make_app(100, 26), Todo("read", "active"))
    app._handle_event(result_event("src/app.py", 1, 0))
    app._submit()
    assert app._show_sidebar is True


def test_a_pinned_off_panel_ignores_new_work():
    app = give_plan(make_app(100, 26), Todo("read", "active"))
    app._handle_key("ctrl+t")
    assert app._show_sidebar is False
    app._handle_event(todo_result({"text": "more", "status": "pending"}))
    assert app._show_sidebar is False
    assert not has_panel(app)


def test_a_pinned_on_panel_stays_on_with_nothing_in_it():
    app = make_app(100, 26)
    app._handle_key("ctrl+t")
    assert app._show_sidebar is True
    assert has_panel(app)
    app._submit()
    assert app._show_sidebar is True


def test_folding_is_not_a_pin():
    app = give_plan(make_app(100, 26), Todo("read", "active"))
    app._handle_key("ctrl+g")
    assert app._sidebar_auto is True
    assert prefs.sidebar_pinned() is False
    assert app._show_sidebar is True


def test_settings_toggles_what_is_on_screen():
    app = give_plan(make_app(100, 26), Todo("read", "active"))
    from vx.app import _graft_commands
    from vx.commands import CommandsMixin

    _graft_commands(type(app), CommandsMixin)
    app._handle_settings_select(footer.MenuItem(label="sidebar", value="sidebar"))
    assert app._show_sidebar is False
    assert app._sidebar_auto is False
    app._handle_settings_select(footer.MenuItem(label="sidebar", value="sidebar"))
    assert app._show_sidebar is True


def test_a_replayed_session_reopens_the_panel():
    app = make_app(100, 26)
    assert not app._show_sidebar
    app._render_history_messages(
        [
            ToolResultMessage(
                tool_call_id="t1",
                tool_name="todo",
                content=[TextContent(text="Plan")],
                ui_data={"todos": [{"text": "read", "status": "done"}]},
            )
        ]
    )
    assert app._show_sidebar is True


def test_a_new_conversation_takes_the_panel_with_it():
    app = give_plan(make_app(100, 26), Todo("read", "active"))
    app._sidebar.touches = [Touch("a.py", 1, 0)]
    app._reset_sidebar()
    assert app._show_sidebar is False
    assert app._sidebar.todos == []
    assert app._sidebar.touches == []
    assert not has_panel(app)
