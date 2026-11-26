"""The terminal drawer: a box over the transcript, its keys, and its kill."""

import asyncio
from typing import cast

import pytest
from vx import prefs
from vx.app import VxApp
from vx.jobs import REGISTRY, Job
from vx.tui import ansi, blocks, panels
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
    monkeypatch.setattr(prefs, "prefs_path", lambda: tmp_path / "vx-prefs.json")


@pytest.fixture(autouse=True)
def _quiet_registry():
    REGISTRY.jobs.clear()
    REGISTRY._next = 0
    yield
    REGISTRY.jobs.clear()
    REGISTRY._next = 0


def plain(rows: list[str]) -> list[str]:
    return [ansi.strip_ansi(row).rstrip() for row in rows]


def make_job(*lines: str, status: str = "running", exit_code: int | None = None) -> Job:
    job = Job(id="j1", command="npm run dev", proc=None, group=None, status=status)
    job.exit_code = exit_code
    if status != "running":
        job.ended = job.started
    for line in lines:
        job.write("out", f"{line}\n")
    return job


def make_app(cols: int = 100, rows: int = 26) -> VxApp:
    app = VxApp(cwd="/tmp", terminal=cast(Terminal, StubTerminal(cols, rows)))
    app._ready = asyncio.Event()
    app._ready.set()
    return app


# -- the box ---------------------------------------------------------------


def test_the_box_is_bounded_on_every_axis():
    assert panels.TerminalPanel(job=make_job("hi")).body()
    assert panels.TerminalPanel.height(40) == panels.MAX_HEIGHT
    assert panels.TerminalPanel.height(6) <= 6
    assert panels.TerminalPanel.width(200) == 88


def test_no_row_is_wider_than_the_box():
    job = make_job(*[f"line {i} " + "x" * 200 for i in range(30)])
    for width in (20, 30, 44, 88):
        for height in (5, 7, 12):
            for row in panels.TerminalPanel(job).rows(width, height):
                assert ansi.width(row) <= width, (width, height, row)


def test_every_row_of_the_box_is_the_box_width():
    job = make_job(*[f"line {i} " + "x" * 200 for i in range(40)])
    job.command = "npm run dev --host 0.0.0.0 --port 3000"
    for width in (20, 30, 60, 84, 88):
        for height in (5, 8, 12):
            rows = panels.TerminalPanel(job).rows(width, height)
            assert [ansi.width(row) for row in rows] == [width] * len(rows)


def test_the_title_names_the_job_and_its_state():
    panel = panels.TerminalPanel(job=make_job("boot"))
    rows = plain(panel.rows(60, 7))
    assert "j1 · npm run dev" in rows[0]
    assert "running" in rows[0]
    assert "boot" in rows[1]
    assert "esc close" in rows[-1]


def test_a_finished_job_says_how_it_ended_and_drops_the_kill_key():
    panel = panels.TerminalPanel(job=make_job("boom", status="failed", exit_code=2))
    text = "\n".join(plain(panel.rows(60, 7)))
    assert "exit 2" in text
    assert "k kill" not in text


def test_a_running_job_shows_its_tail_not_its_first_lines():
    panel = panels.TerminalPanel(job=make_job(*[f"l{i}" for i in range(40)]))
    text = "\n".join(plain(panel.rows(60, 7)))
    assert "36-40/40" in text
    assert "l0" not in text


def test_short_output_needs_no_range():
    panel = panels.TerminalPanel(job=make_job("one", "two"))
    text = "\n".join(plain(panel.rows(60, 8)))
    assert "1-2/2" in text


def test_an_empty_job_says_zero_lines():
    panel = panels.TerminalPanel(job=make_job())
    assert "0 lines" in "\n".join(plain(panel.rows(60, 6)))


def test_scrolling_stops_at_the_ends():
    panel = panels.TerminalPanel(job=make_job(*[f"l{i}" for i in range(40)]))
    panel.follow = False
    panel.scroll = 10_000
    assert "l39" in "\n".join(plain(panel.rows(60, 7)))
    panel.scroll = -5
    assert "l0" in "\n".join(plain(panel.rows(60, 7)))


def test_end_rejoins_the_tail():
    panel = panels.TerminalPanel(job=make_job(*[f"l{i}" for i in range(40)]))
    panel.follow, panel.scroll = False, 0
    assert "l0" in "\n".join(plain(panel.rows(60, 7)))
    panel.follow, panel.scroll = True, 0
    assert "l39" in "\n".join(plain(panel.rows(60, 7)))


# -- in the frame ----------------------------------------------------------


def open_drawer(app: VxApp, job: Job) -> VxApp:
    REGISTRY.jobs[job.id] = job
    REGISTRY._next = 1
    app._open_terminal(job.id)
    app._draw(force=True)
    return app


def test_the_drawer_sits_over_the_transcript_and_leaves_the_panel_alone():
    app = make_app(100, 26)
    app.viewport.add(blocks.Assistant(text="hello"))
    app._sidebar.todos = []
    REGISTRY.jobs["j1"] = make_job(*[f"l{i}" for i in range(20)])
    REGISTRY._next = 1
    app._sidebar.jobs = [panels_job_row()]
    app._sync_sidebar()
    app._open_terminal("j1")
    app._draw(force=True)
    frame = plain(app._frame)
    assert any("j1 · npm run dev" in row for row in frame)
    # the panel still draws its own list beside the drawer
    assert sum("TERMINALS" in row for row in frame) == 1
    assert all(ansi.width(row) <= 100 for row in app._frame)


def panels_job_row():
    from vx.tui.sidebar import JobRow

    return JobRow("j1", "npm run dev", "running", "3s")


def test_escape_closes_the_drawer_before_it_interrupts():
    app = open_drawer(make_app(), make_job("hi"))
    assert app._terminal is not None
    app._handle_key("esc")
    assert app._terminal is None


def test_the_arrows_scroll_and_end_jumps_to_the_tail():
    app = open_drawer(make_app(), make_job(*[f"l{i}" for i in range(60)]))
    panel = app._terminal
    assert panel is not None
    app._handle_key("end")
    assert panel.scroll == panel.max_scroll()
    app._handle_key("home")
    assert panel.scroll == 0
    app._handle_key("down")
    assert panel.scroll == 1


@pytest.mark.asyncio
async def test_k_kills_the_job_and_the_row_follows():
    job = make_job("hi")
    app = open_drawer(make_app(), job)
    app._handle_key("k")
    await asyncio.sleep(0.1)
    assert job.status == "stopped"
    assert "exit" in job.state_text()


def test_a_wheel_over_the_transcript_leaves_the_drawer_behind():
    app = open_drawer(make_app(), make_job(*[f"l{i}" for i in range(60)]))
    for index in range(40):
        app.viewport.add(blocks.Assistant(text=f"line {index} " + "x" * 60))
    app._draw(force=True)
    rect = app._drawer_rect
    assert rect is not None
    above = rect[1] - 1
    if above >= 1:
        app._mouse_key(f"mouse:wheelup:10:{above}")
        assert app.viewport.scroll > 0


def test_the_wheel_over_the_drawer_scrolls_the_job():
    app = open_drawer(make_app(), make_job(*[f"l{i}" for i in range(60)]))
    panel = app._terminal
    assert panel is not None
    rect = app._drawer_rect
    assert rect is not None
    y = rect[1] + 2
    app._mouse_key(f"mouse:wheeldown:20:{y + 1}")
    assert panel.scroll > 0


# -- frames ----------------------------------------------------------------


class CountingTerminal(StubTerminal):
    """A terminal that remembers what it was written."""

    def __init__(self, cols: int = 100, rows: int = 26) -> None:
        super().__init__(cols, rows)
        self.writes: list[str] = []

    def write(self, data: str) -> None:
        self.writes.append(data)


def counting_app(cols: int = 100, rows: int = 26) -> tuple[VxApp, CountingTerminal]:
    term = CountingTerminal(cols, rows)
    app = VxApp(cwd="/tmp", terminal=cast(Terminal, term))
    app._ready = asyncio.Event()
    app._ready.set()
    for index in range(40):
        app.viewport.add(blocks.Assistant(text=f"row {index} " + "word " * 30))
    app._draw(force=True)
    term.writes.clear()
    return app, term


def test_a_burst_of_wheel_reports_paints_one_frame():
    """A trackpad sends a dozen reports per gesture; that is one repaint."""
    app, term = counting_app()
    app._batching = True
    try:
        for _ in range(12):
            app._mouse_key("mouse:wheelup:10:3")
    finally:
        app._batching = False
    assert term.writes == []
    app._flush_draw()
    assert len(term.writes) == 1


def test_a_key_outside_a_batch_still_paints_at_once():
    app, term = counting_app()
    app._mouse_key("mouse:wheelup:10:3")
    assert len(term.writes) == 1
