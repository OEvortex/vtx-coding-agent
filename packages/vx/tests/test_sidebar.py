"""The sidebar: the plan and the terminals, real data, and the frame around it."""

import asyncio
from typing import cast

import pytest
from ctx.core.events import ToolResultEvent
from ctx.protocol.types import TextContent, ToolResultMessage
from vx import prefs
from vx.app import VxApp, _job_rows
from vx.jobs import REGISTRY, Job
from vx.tui import ansi, blocks
from vx.tui.sidebar import JobRow, Sidebar, Todo, sidebar_cols
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


@pytest.fixture(autouse=True)
def _quiet_registry():
    """A job another test started must not appear in this one's panel."""
    REGISTRY.jobs.clear()
    REGISTRY._next = 0
    yield
    REGISTRY.jobs.clear()
    REGISTRY._next = 0


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


def give_jobs(app: VxApp, *rows: JobRow) -> VxApp:
    app._sidebar.jobs = list(rows)
    app._sync_sidebar()
    return app


def has_panel(app: VxApp) -> bool:
    app._draw(force=True)
    return any(("PLAN" in row or "TERMINALS" in row) for row in plain(app._frame))


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
    panel = Sidebar([Todo("read it", "done")], [JobRow("j1", "npm run dev", label="3s")])
    for height in (3, 4, 7, 12, 30):
        assert len(panel.rows(30, height)) == height
    assert len(Sidebar().rows(30, 5)) == 5


def test_no_row_is_wider_than_the_panel():
    todos = [Todo("a step " * 12, "active"), Todo("another step", "done")]
    jobs = [JobRow("j1", "npm run dev --host 0.0.0.0 " * 2, "running", "12s")]
    for width in (12, 18, 24, 30, 61):
        for height in range(3, 40):
            for todos_open in (True, False):
                for jobs_open in (True, False):
                    panel = Sidebar(todos, jobs, todos_open, jobs_open)
                    for row in panel.rows(width, height):
                        assert ansi.width(row) <= max(width, 0), (width, height, row)


# -- the plan --------------------------------------------------------------


def test_the_plan_shows_status_glyphs_and_a_count():
    panel = Sidebar([Todo("read", "done"), Todo("write", "active"), Todo("test")])
    text = "\n".join(plain(panel.rows(30, 12)))
    assert "1/3" in text
    assert "✓ read" in text
    assert "▸ write" in text
    assert "○ test" in text


def test_the_header_is_a_grid_and_the_rail_spans_the_panel():
    panel = Sidebar([Todo("read", "done"), Todo("write", "active")])
    rows = plain(panel.rows(30, 12))
    assert rows[0].startswith("▾ PLAN")
    assert rows[0].endswith("1/2")
    assert "━" in rows[1]


def test_a_folded_plan_keeps_the_active_step():
    # Folded, the section still answers "what is it doing now".
    panel = Sidebar([Todo("read", "done"), Todo("write the panel", "active")], todos_open=False)
    rows = plain(panel.rows(30, 8))
    assert "▸ PLAN" in rows[0]
    assert "write the panel" in rows[1]
    assert "read" not in "\n".join(rows)


def test_a_long_plan_says_how_much_is_cut_not_that_it_ended():
    panel = Sidebar([Todo(f"step {i}") for i in range(12)])
    text = "\n".join(plain(panel.rows(30, 12)))
    assert "more…" in text
    assert "12" not in text.split("more…")[0].splitlines()[-1]


# -- the terminals ---------------------------------------------------------


def test_the_terminals_header_counts_running_out_of_total():
    panel = Sidebar(
        [Todo("read", "active")],
        [
            JobRow("j1", "npm run dev", "running", "12s"),
            JobRow("j2", "uv run pytest -q", "done", "4s"),
            JobRow("j3", "make release", "failed", "2 · 11s"),
        ],
    )
    rows = plain(panel.rows(30, 20))
    assert "1/3" in "\n".join(rows)
    assert "TERMINALS" in "\n".join(rows)


def test_every_terminal_row_is_openable():
    panel = Sidebar(
        [], [JobRow("j1", "npm run dev", "running", "12s"), JobRow("j2", "pytest", "done", "4s")]
    )
    panel.rows(30, 20)
    assert sorted(panel.job_hits.values()) == ["j1", "j2"]
    for row in plain(panel.rows(30, 20)):
        if "npm run dev" in row or "pytest" in row:
            assert row.rstrip().endswith("›")


def test_a_folded_section_keeps_its_header_only():
    panel = Sidebar([], [JobRow("j1", "npm run dev", "running", "12s")], jobs_open=False)
    rows = plain(panel.rows(30, 20))
    assert any("▸ TERMINALS" in row for row in rows)
    assert "npm run dev" not in "\n".join(rows)


def test_finished_jobs_show_how_they_ended():
    panel = Sidebar(
        [],
        [JobRow("j3", "make release", "failed", "2 · 11s"), JobRow("j1", "dev", "running", "9s")],
    )
    text = "\n".join(plain(panel.rows(30, 16)))
    assert "2 · 11s" in text
    assert "✕" in text
    assert "●" in text


def test_the_registry_is_what_the_panel_draws():
    REGISTRY.jobs["j1"] = Job(id="j1", command="npm run dev", proc=None, group=None)
    REGISTRY._next = 1
    assert [row.id for row in _job_rows()] == ["j1"]
    assert _job_rows()[0].status == "running"


# -- the frame -------------------------------------------------------------


def test_the_panel_sits_beside_the_transcript_and_the_footer_spans_both():
    app = make_app(100, 26)
    app.viewport.add(blocks.Assistant(text="hello"))
    give_plan(app, Todo("read", "active"))
    app._draw(force=True)
    frame = app._frame
    assert len(frame) == 26
    assert all(ansi.width(row) <= 100 for row in frame)
    assert "PLAN" in frame[0]
    # The rule is one column past the transcript, on every body row.
    for row in frame[:20]:
        assert ansi.strip_ansi(row)[73] == "│"
    # The footer is the terminal's width, not the transcript's.
    assert ansi.width(frame[-1]) == 100


def test_ctrl_t_is_not_a_panel_toggle():
    app = give_plan(make_app(100, 26), Todo("read", "active"))
    assert has_panel(app)
    app._handle_key("ctrl+t")
    assert has_panel(app)


def test_ctrl_g_closes_both_sections():
    app = make_app(100, 26)
    app._sidebar.todos = [Todo("read", "active")]
    app._sidebar.jobs = [JobRow("j1", "dev", "running", "2s")]
    app._handle_key("ctrl+g")
    assert not app._sidebar.todos_open and not app._sidebar.jobs_open
    app._handle_key("ctrl+g")
    assert app._sidebar.todos_open and app._sidebar.jobs_open


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


# -- clicks ----------------------------------------------------------------


def sidebar_x(app: VxApp) -> int:
    return app._side_main + 2


def test_clicking_a_terminal_row_opens_its_drawer():
    app = make_app(100, 26)
    REGISTRY.jobs["j1"] = Job(id="j1", command="npm run dev", proc=None, group=None)
    REGISTRY._next = 1
    give_jobs(app, JobRow("j1", "npm run dev", "running", "2s"))
    app._draw(force=True)
    row = next(iter(app._sidebar.job_hits))
    app._mouse_key(f"mouse:left:{sidebar_x(app)}:{row + 1}")
    assert app._terminal is not None
    assert app._terminal.job is REGISTRY.jobs["j1"]


def test_clicking_the_section_header_folds_it():
    app = make_app(100, 26)
    give_plan(app, Todo("read", "active"))
    app._draw(force=True)
    app._mouse_key(f"mouse:left:{sidebar_x(app)}:{cast(int, app._sidebar.tasks_header_row) + 1}")
    assert not app._sidebar.todos_open
    app._mouse_key(f"mouse:left:{sidebar_x(app)}:{cast(int, app._sidebar.tasks_header_row) + 1}")
    assert app._sidebar.todos_open


def test_a_click_in_the_transcript_is_not_a_panel_click():
    app = make_app(100, 26)
    give_plan(app, Todo("read", "active"))
    app._draw(force=True)
    app._mouse_key(f"mouse:left:10:{cast(int, app._sidebar.tasks_header_row) + 1}")
    assert app._sidebar.todos_open


# -- real events -----------------------------------------------------------


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


def test_a_started_job_opens_the_panel_by_itself():
    app = make_app(100, 26)
    REGISTRY.jobs["j1"] = Job(id="j1", command="npm run dev", proc=None, group=None)
    REGISTRY._next = 1
    assert has_panel(app)


def test_a_replayed_session_gets_its_plan_back():
    app = make_app()
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
    assert [t.text for t in app._sidebar.todos] == ["read"]


# -- the panel follows the work --------------------------------------------


def test_the_panel_follows_the_work_by_default():
    assert make_app(100, 26)._show_sidebar is False


def test_stale_pin_preferences_are_ignored():
    prefs.prefs_path().write_text(
        '{"sidebar_pinned": true, "sidebar_shown": true}', encoding="utf-8"
    )
    app = make_app(100, 26)
    assert app._show_sidebar is False
    assert not has_panel(app)


def test_folded_sections_come_back_folded():
    give_plan(make_app(100, 26), Todo("read", "active"))._handle_key("ctrl+g")
    fresh = make_app(100, 26)
    assert not fresh._sidebar.todos_open
    assert not fresh._sidebar.jobs_open
    fresh._sidebar.todos = [Todo("read", "active")]
    assert fresh._sidebar.rows(30, 10)[0].find("PLAN") >= 0


def test_a_prefs_file_from_before_the_terminals_keeps_its_fold():
    prefs.prefs_path().write_text('{"sidebar_files_open": false}', encoding="utf-8")
    _plan, jobs_open = prefs.sidebar_folds()
    assert jobs_open is False


def test_fold_preferences_survive_a_corrupt_file():
    prefs.prefs_path().write_text("{not json", encoding="utf-8")
    assert prefs.sidebar_folds() == (True, True)
    assert make_app(100, 26)._show_sidebar is False


def test_a_non_boolean_fold_pref_falls_back_to_the_default():
    prefs.prefs_path().write_text('{"sidebar_plan_open": "yes"}', encoding="utf-8")
    assert prefs.sidebar_folds() == (True, True)


# -- scrolling beside the panel ---------------------------------------------


def tall_app(cols: int = 140, rows: int = 30) -> VxApp:
    """A transcript long enough to scroll, with the plan open."""
    app = give_plan(make_app(cols, rows), Todo("wire the fix", "active"))
    for index in range(40):
        app.viewport.add(blocks.Assistant(text=f"row {index} " + "word " * 30))
    app._draw(force=True)
    return app


def test_scrolling_uses_the_transcript_column_not_the_terminal_width():
    """The scroll width must be the width the frame was painted at.

    Scrolling at the terminal width rebuilt every block at a width no frame
    used, and the rewrap delta landed in the scroll position: one wheel notch
    moved the window dozens of rows and the whole transcript was re-rendered
    twice per event.
    """
    app = tall_app()
    assert 0 < app._side_main < 140
    assert app._size()[0] == app._side_main


def test_one_wheel_notch_moves_three_rows():
    app = tall_app()
    app.viewport.bottom()
    app._mouse_key("mouse:wheelup:10:3")
    assert app.viewport.scroll == 3
    app._mouse_key("mouse:wheeldown:10:3")
    assert app.viewport.scroll == 0


def test_an_arrow_scrolls_a_few_lines_rather_than_a_screen():
    app = tall_app()
    app.viewport.bottom()
    app._handle_key("up")
    assert app.viewport.scroll == 3
    app._handle_key("down")
    assert app.viewport.scroll == 0
    app._handle_key("pageup")
    assert app.viewport.scroll > 3


def test_streaming_while_scrolled_back_holds_the_readers_row():
    """A reply streaming in must not drag the window out from under a reader."""
    app = tall_app()
    live = blocks.Assistant(text="first line\n\n")
    app.viewport.add(live)
    app._draw(force=True)
    app.viewport.scroll_by(6, *app._size())
    app._draw()
    top = app._frame[2]
    for _ in range(30):
        live.text += "another sentence that streams in while you read.\n\n"
        app.viewport.touch()
        app._draw()
    assert app._frame[2] == top
    assert app.viewport.unseen > 0
