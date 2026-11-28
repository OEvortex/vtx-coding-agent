"""The swarm board page: the snapshot it draws, its geometry, and its keys."""

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import cast

import pytest
from vx.app import VxApp
from vx.tui import ansi, theme
from vx.tui.swarm import (
    MIN_COLS,
    MIN_ROWS,
    Ask,
    MemberView,
    PostView,
    SwarmBoard,
    SwarmView,
    snapshot_from_engine,
    tool_style,
)

# -- a live engine, in the shape the page reads ------------------------


@dataclass
class FakeRow:
    role: str = "worker"
    model: str = "swe-2-flash"
    channels: list[str] = field(default_factory=list)
    workspace: str = "inherit"
    last_read_seq: int = 0
    added_by: str = "coordinator"
    note: str = ""
    killed: bool = False


@dataclass
class FakeBoard:
    title: str = "the parser rewrite"
    posts: list = field(default_factory=list)
    channels: dict = field(default_factory=dict)

    def since(self, seq: int, limit: int | None = None, channels: set | None = None):
        out = [post for post in self.posts if post.seq > seq]
        return out[-limit:] if limit else out


@dataclass
class FakeLive:
    row: FakeRow
    state: str = "working"
    turns: int = 3
    started_at: float = 0.0
    last_text: str = ""
    pending_model: str = ""
    activity: list = field(default_factory=list)
    current: object | None = None
    tools: int = 0
    fails: int = 0
    stream: str = ""


@dataclass
class FakeActivity:
    """One engine-side tool call, in the shape the page reads."""

    label: str = "bash"
    detail: str = "cargo test -p parser"
    status: str = "ok"
    at: float = 0.0
    took: float = 1.2
    kind: str = "tool"
    agent: str = "parser"
    channel: str = "main"
    output: str = ""


def fake_engine(now: float = 0.0):
    posts = [
        SimpleNamespace(
            seq=1,
            agent="coordinator",
            text="@parser own src/parser.rs",
            channel="main",
            time="09:02",
        ),
        SimpleNamespace(
            seq=2, agent="parser", text="@verifier lexer passes", channel="parser", time="09:05"
        ),
        SimpleNamespace(
            seq=3, agent="verifier", text="@parser #2 fails on 1n", channel="parser", time="09:09"
        ),
    ]
    parser = FakeLive(
        FakeRow(channels=["parser"], workspace="share", last_read_seq=1), last_text="lexer passes"
    )
    verifier = FakeLive(
        FakeRow(role="verifier", channels=["parser"], last_read_seq=3), state="waiting"
    )
    docs = FakeLive(FakeRow(model="swe-2-mini", last_read_seq=0), state="stopped")
    dead = FakeLive(FakeRow(killed=True), state="killed")
    return SimpleNamespace(
        board=FakeBoard(posts=posts, channels={"parser": "lexer and parser"}),
        members={"parser": parser, "verifier": verifier, "docs": docs, "old-worker": dead},
        dir="/tmp/swarm",
        started=now,
    )


def live_board(width: int = 100, height: int = 30) -> SwarmBoard:
    return SwarmBoard(view=snapshot_from_engine(fake_engine(now=600.0), now=600.0))


# -- the snapshot ------------------------------------------------------


class TestSnapshot:
    def test_no_engine_is_an_empty_page_not_a_crash(self):
        view = snapshot_from_engine(None)
        assert view.exists is False
        rows = SwarmBoard(view=view).render(100, 24)
        text = "\n".join(ansi.strip_ansi(row) for row in rows)
        assert "no swarm in this conversation" in text

    def test_every_member_and_post_crosses_over(self):
        view = snapshot_from_engine(fake_engine(now=600.0), now=600.0)
        assert [m.name for m in view.members] == ["parser", "verifier", "docs", "old-worker"]
        assert view.head == 3
        assert view.channels == {"parser": "lexer and parser"}
        parser = view.member("parser")
        assert parser is not None
        assert parser.state == "working"
        assert parser.activity == "lexer passes"
        assert parser.workspace == "share"
        verifier = view.member("verifier")
        assert verifier is not None
        assert "parked" in verifier.activity
        killed = view.member("old-worker")
        assert killed is not None
        assert killed.state == "killed"

    def test_the_roster_orders_working_before_parked_before_killed(self):
        view = snapshot_from_engine(fake_engine(), now=0.0)
        assert [m.name for m in view.ordered()] == ["parser", "verifier", "docs", "old-worker"]


# -- geometry ----------------------------------------------------------


class TestGeometry:
    @pytest.mark.parametrize("width", [20, 40, 51, 52, 60, 72, 83, 84, 100, 120, 160, 200])
    @pytest.mark.parametrize("height", [6, 9, 10, 12, 16, 24, 30, 40, 60])
    def test_a_frame_is_exactly_the_size_asked_for(self, width: int, height: int):
        rows = live_board().render(width, height)
        assert len(rows) == height
        assert all(ansi.width(row) == width for row in rows)

    def test_a_tiny_terminal_says_what_it_needs(self):
        text = "\n".join(
            ansi.strip_ansi(r) for r in live_board().render(MIN_COLS - 4, MIN_ROWS - 2)
        )
        assert f"{MIN_COLS}x{MIN_ROWS}" in text

    def test_every_band_lands_at_a_useful_size(self):
        text = "\n".join(ansi.strip_ansi(r) for r in live_board().render(110, 32))
        assert "the parser rewrite" in text
        assert "WORKING" in text and "WAITING" in text and "KILLED" in text
        assert "BOARD" in text and "#parser" in text
        assert "board #3" in text

    def test_the_feed_is_bottom_anchored(self):
        rows = [ansi.strip_ansi(r) for r in live_board().render(110, 34)]
        newest = next(i for i, row in enumerate(rows) if "#3" in row and "@verifier" in row)
        assert newest > 20  # the newest post sits near the keys, not under the header

    def test_a_members_panel_shows_its_activity_and_its_posts(self):
        board = live_board()
        assert board.key("enter") == "redraw"
        text = "\n".join(ansi.strip_ansi(r) for r in board.render(110, 30))
        assert "MEMBER" in text
        assert "ACTIVITY" in text
        assert "POSTS (1)" in text
        assert "#2" in text  # parser's own post

    def test_help_is_a_box_inside_the_frame(self):
        board = live_board()
        assert board.key("?") == "redraw"
        rows = board.render(100, 26)
        text = "\n".join(ansi.strip_ansi(r) for r in rows)
        assert "open the member's panel" in text
        assert all(ansi.width(row) == 100 for row in rows)


# -- keys --------------------------------------------------------------


class TestKeys:
    def test_selection_walks_the_roster_and_clamps(self):
        board = live_board()
        for _ in range(10):
            board.key("down")
        assert board.selected == 3
        for _ in range(10):
            board.key("up")
        assert board.selected == 0

    def test_enter_opens_and_esc_backs_out_then_closes(self):
        board = live_board()
        assert board.key("enter") == "redraw"
        assert board.open_member == "parser"
        assert board.key("esc") == "redraw"
        assert board.open_member == ""
        assert board.key("left") == "close"

    def test_tab_moves_the_keys_to_the_feed_and_scrolling_holds_the_tail(self):
        board = live_board()
        board.key("tab")
        assert board.focus == "feed"
        board.key("up")
        assert board.follow is False
        board.key("f")
        assert board.follow is True

    def test_channels_are_numbered(self):
        board = live_board()
        board.key("2")  # all, main, parser...
        assert board.channel == "main"
        board.key("3")
        assert board.channel == "parser"
        text = "\n".join(ansi.strip_ansi(r) for r in board.render(100, 24))
        assert "every channel" not in text

    def test_pause_and_help_are_sticky_until_toggled(self):
        board = live_board()
        board.key(" ")
        assert board.paused is True
        board.key(" ")
        assert board.paused is False
        board.key("?")
        assert board.help is True
        board.key("esc")
        assert board.help is False
        assert board.open_member == ""

    def test_unknown_keys_are_ignored(self):
        assert live_board().key("ctrl+z") == "ignore"


# -- needs you ---------------------------------------------------------


def blocked_board() -> SwarmBoard:
    board = live_board()
    board.ask = Ask(member="parser", label="bash", target="uv run pytest -x -q")
    return board


class TestAsk:
    def test_the_band_names_the_worker_the_tool_and_the_command(self):
        rows = blocked_board().render(110, 30)
        text = "\n".join(ansi.strip_ansi(r) for r in rows)
        assert "@parser" in text and "bash" in text
        assert "uv run pytest -x -q" in text
        assert all(ansi.width(row) == 110 for row in rows)

    def test_the_footer_switches_to_the_answers(self):
        text = "\n".join(ansi.strip_ansi(r) for r in blocked_board().render(110, 30))
        assert "1-3 answer" in text
        assert "select" not in text.split("1-3 answer")[-1]

    def test_enter_approves_and_esc_denies(self):
        board = blocked_board()
        assert board.key("enter") == "approve"
        board.ask = Ask(member="parser", label="bash")
        assert board.key("esc") == "deny"

    def test_the_numbers_pick_the_answer(self):
        board = blocked_board()
        assert board.key("1") == "approve"
        board.ask = Ask(member="parser", label="bash")
        assert board.key("2") == "allow"
        board.ask = Ask(member="parser", label="bash")
        assert board.key("3") == "deny"

    def test_arrows_move_the_selection_and_nothing_else_is_live(self):
        board = blocked_board()
        assert board.key("down") == "redraw"
        assert board.ask is not None and board.ask.selected == 1
        assert board.key("f") == "redraw"  # not a follow toggle while a human decides
        assert board.ask is not None and board.ask.selected == 1

    def test_a_blocked_member_is_first_in_the_roster_and_counted_in_the_header(self):
        view = snapshot_from_engine(fake_engine())
        view.members[0].state = "blocked"
        view.members[0].activity = "needs you · bash uv run pytest"
        board = SwarmBoard(view=view)
        assert board.view.ordered()[0].name == "parser"
        text = "\n".join(ansi.strip_ansi(r) for r in board.render(110, 30))
        assert "NEEDS YOU" in text or "needs you" in text
        assert "1 needs you" in text


# -- the app -----------------------------------------------------------


class StubTerminal:
    _fd = -1

    def __init__(self) -> None:
        self.out: list[str] = []

    def size(self) -> tuple[int, int]:
        return 30, 100

    def write(self, data: str) -> None:
        self.out.append(data)

    def enter(self) -> None: ...

    def exit(self) -> None: ...


def make_app(term: StubTerminal | None = None) -> tuple[VxApp, StubTerminal]:
    terminal = term or StubTerminal()
    app = VxApp(cwd="/tmp", terminal=cast("object", terminal))  # type: ignore[arg-type]
    app._ready = asyncio.Event()
    app._ready.set()
    return app, terminal


class TestApp:
    def test_left_on_an_empty_composer_opens_the_board_and_esc_closes_it(self):
        app, term = make_app()
        app._handle_key("left")
        assert app._swarm is not None
        app._draw(force=True)
        assert term.out, "the page painted nothing"
        app._handle_key("esc")
        assert app._swarm is None

    def test_left_with_text_moves_the_cursor_instead(self):
        app, _term = make_app()
        app._input = "abc"
        app._cursor = 3
        app._handle_key("left")
        assert app._swarm is None
        assert app._cursor == 2

    def test_alt_s_opens_the_board_from_the_composer(self):
        app, _term = make_app()
        app._input = "typing"
        app._handle_key("alt+s")
        assert app._swarm is not None
        assert app._swarm_key("alt+s") is True  # the same key closes it
        assert app._swarm is None

    def _key(self, app: VxApp, key: str) -> bool:
        return app._swarm_key(key)

    def test_slash_board_opens_it_and_the_page_owns_the_keys(self, monkeypatch):
        monkeypatch.setattr("vx.swarm.peek_engine", lambda ctx=None: fake_engine())
        app, _term = make_app()
        assert app._handle_slash("/board") is True
        board = app._swarm
        assert board is not None
        assert [m.name for m in board.view.members] == ["parser", "verifier", "docs", "old-worker"]
        app._handle_key("down")
        assert app._swarm is not None
        assert app._swarm.selected == 1
        # The state is live: a refresh re-reads the engine, not a cached copy.
        app._swarm_key("enter")
        assert app._swarm.open_member == "verifier"
        # ctrl+c still belongs to the app, not the page.
        assert self._key(app, "ctrl+c") is False

    def test_the_page_paints_the_live_swarm(self, monkeypatch):
        monkeypatch.setattr("vx.swarm.peek_engine", lambda ctx=None: fake_engine())
        app, term = make_app()
        app._handle_key("alt+s")
        app._draw(force=True)
        painted = ansi.strip_ansi("".join(term.out))
        assert "the parser rewrite" in painted
        assert "parser" in painted and "BOARD" in painted

    @pytest.mark.asyncio
    async def test_a_worker_tool_call_lands_on_the_page_and_is_answered(self, monkeypatch):
        from ctx.core import ApprovalResponse

        monkeypatch.setattr("vx.swarm.peek_engine", lambda ctx=None: fake_engine())
        app, _term = make_app()
        app._handle_key("alt+s")
        event = SimpleNamespace(
            tool_name="bash",
            display="rm -rf build",
            future=asyncio.get_running_loop().create_future(),
        )
        task = asyncio.ensure_future(app._swarm_approval(SimpleNamespace(name="parser"), event))
        await asyncio.sleep(0)
        app._draw(force=True)
        board = app._swarm
        assert board is not None and board.ask is not None
        assert (board.ask.member, board.ask.label) == ("parser", "bash")
        assert "rm -rf build" in "\n".join(ansi.strip_ansi(r) for r in board.render(110, 30))
        app._handle_key("3")  # deny
        assert await task is ApprovalResponse.DENY
        assert board.ask is None

    @pytest.mark.asyncio
    async def test_allowing_a_tool_covers_the_next_worker_too(self, monkeypatch):
        from ctx.core import ApprovalResponse

        monkeypatch.setattr("vx.swarm.peek_engine", lambda ctx=None: fake_engine())
        app, _term = make_app()
        app._handle_key("alt+s")

        async def ask(name: str) -> object:
            event = SimpleNamespace(
                tool_name="bash", display="ls", future=asyncio.get_running_loop().create_future()
            )
            task = asyncio.ensure_future(app._swarm_approval(SimpleNamespace(name=name), event))
            await asyncio.sleep(0)
            return task

        task = await ask("parser")
        app._handle_key("2")  # allow bash for this swarm
        assert await task is ApprovalResponse.APPROVE
        assert "bash" in app._swarm_allowed
        # The second worker is not asked again: the swarm already said yes.
        task = await ask("verifier")
        assert task.done()
        assert await task is ApprovalResponse.APPROVE
        assert app._swarm_asks == []

    @pytest.mark.asyncio
    async def test_closing_the_board_denies_what_it_was_showing(self):
        from ctx.core import ApprovalResponse

        app, _term = make_app()
        app._handle_key("alt+s")
        future = asyncio.get_running_loop().create_future()
        app._swarm_asks.append(("parser", "bash", "rm -rf build", SimpleNamespace(future=future)))
        app._close_swarm()
        assert future.result() is ApprovalResponse.DENY
        assert app._swarm_asks == []

    def test_the_page_paints_without_a_swarm_in_the_conversation(self):
        app, term = make_app()
        app._handle_key("alt+s")
        app._draw(force=True)
        painted = "".join(term.out)
        assert "vx swarm" in ansi.strip_ansi(painted)


@pytest.mark.asyncio
async def test_the_snapshot_reads_a_real_engine(tmp_path):
    """The adapter works on the engine itself, not only on a stand-in.

    The workers are scripted (no model), but the roster, the board, the channels
    and the states are the real ``SwarmEngine`` writing real files.
    """
    from types import SimpleNamespace

    from vx.swarm.engine import SwarmEngine

    parent = SimpleNamespace(
        cwd=str(tmp_path),
        model="test-model",
        model_provider=None,
        base_url=None,
        thinking_level="high",
        provider=None,
        session=None,
    )
    engine = SwarmEngine(parent, root=tmp_path / "swarm")
    engine.set_title("the parser rewrite")

    class Parked:
        async def run(self, prompt, cancel_event=None):
            for event in ():
                yield event

    async def build(member, **kwargs):
        member.row.conversation_id = f"conv-{member.name}"
        member.agent = Parked()

    engine._build_agent = build  # type: ignore[method-assign]
    engine.channel_action("create", "parser", "lexer and parser")
    await engine.add("parser", "own the lexer", channels=["parser"])
    await engine.add("verifier", "check the diff", role="verifier", channels=["parser"])
    await engine.wait("coordinator", since=0, timeout=0.05)

    view = snapshot_from_engine(engine)
    assert view.exists
    assert view.title == "the parser rewrite"
    assert {m.name for m in view.members} == {"parser", "verifier"}
    assert view.channels == {"parser": "lexer and parser"}
    assert view.head == 2  # the two briefs the coordinator posted
    parser = view.member("parser")
    assert parser is not None
    assert parser.channels == ("parser",)
    assert parser.state in ("working", "waiting", "stopped")
    assert parser.activity  # whatever state it is in, the card can say something

    text = "\n".join(ansi.strip_ansi(r) for r in SwarmBoard(view=view).render(110, 30))
    assert "parser" in text and "verifier" in text
    # The scripted workers finish their turn, so the header counts them as idle --
    # whatever the count, the header names a state and a number.
    assert any(word in text for word in ("2 idle", "WORKING", "WAITING"))
    assert "#parser" in text and "@parser" in text
    await engine.close()


def test_the_board_and_the_engine_agree_on_state_names():
    """The page's state words are the engine's, plus the one only it can see."""
    from vx.tui.swarm import STATES

    engine_states = {"working", "waiting", "stopped", "killed"}
    assert engine_states < {name for name, _glyph, _word in STATES}


def test_the_key_box_also_answers_on_an_empty_board():
    """A page with nothing in it still has keys, so "?" must not be silent."""
    board = SwarmBoard(view=SwarmView())
    board.key("?")
    rows = board.render(90, 24)
    text = "\n".join(ansi.strip_ansi(r) for r in rows)
    assert "open the member's panel" in text
    assert all(ansi.width(row) == 90 for row in rows)


def test_member_view_channel_defaults_to_main():
    assert MemberView(name="x").channel == "main"
    assert MemberView(name="x", channels=("ui",)).channel == "ui"


def test_a_post_view_renders_its_channel_badge():
    board = SwarmBoard(
        view=SwarmView(
            title="t",
            members=[MemberView(name="parser", channels=("parser",))],
            posts=[PostView(1, "parser", "hello", "parser")],
            channels={"parser": "lexer"},
        )
    )
    text = "\n".join(ansi.strip_ansi(r) for r in board.render(110, 20))
    assert "#parser" in text and "@parser" in text


def test_theme_is_read_at_render_time(monkeypatch):
    """A palette switch reaches the page as soon as it repaints."""
    theme.apply_named("fx-dark")
    board = live_board()
    dark = board.render(100, 24)
    theme.apply_named("fx-light")
    light = board.render(100, 24)
    assert dark != light


# -- live tool calls ---------------------------------------------------


def active_board() -> SwarmBoard:
    """A room where the parser has three calls behind it and one in flight."""
    engine = fake_engine(now=600.0)
    parser = engine.members["parser"]
    parser.activity = [
        FakeActivity(label="read", detail="src/parser.rs L112-240", status="ok", took=0.7),
        FakeActivity(label="edit", detail="src/parser.rs +18 -6", status="ok", took=1.2),
        FakeActivity(label="bash", detail="cargo test --workspace", status="fail", took=3.0),
    ]
    parser.tools, parser.fails = 3, 1
    parser.current = FakeActivity(
        label="grep", detail="fn parse_number across src/", status="run", at=598.5, took=0.0
    )
    return SwarmBoard(view=snapshot_from_engine(engine, now=600.0))


class TestLiveActivity:
    def test_tool_calls_cross_over_with_their_label_and_duration(self):
        view = active_board().view
        parser = view.member("parser")
        assert parser is not None
        assert parser.tools == 3 and parser.fails == 1
        assert [event.label for event in parser.events[:-1]] == ["read", "edit", "bash"]

    def test_a_running_call_is_listed_before_it_finishes(self):
        parser = active_board().view.member("parser")
        assert parser is not None
        running = parser.running
        assert running is not None
        assert running.label == "grep"
        assert running.secs == pytest.approx(1.5)

    def test_the_activity_column_shows_the_calls_and_the_roster_shows_the_live_one(self):
        text = "\n".join(ansi.strip_ansi(r) for r in active_board().render(120, 34))
        assert "grep" in text and "fn parse_number across src/" in text
        assert "1.5s" in text  # the running call's elapsed time
        assert "3.0s" in text  # a finished call's duration
        assert "3⌁ 1✗" in text.replace("  ", " ")

    def test_v_narrows_the_column_to_board_posts(self):
        board = active_board()
        assert board.key("v") == "redraw"
        text = "\n".join(ansi.strip_ansi(r) for r in board.render(120, 34))
        assert "cargo test --workspace" not in text
        assert "lexer passes" in text  # the posts are still there

    def test_a_members_panel_lists_its_own_calls(self):
        board = active_board()
        board.key("enter")
        text = "\n".join(ansi.strip_ansi(r) for r in board.render(120, 34))
        assert "MEMBER" in text
        assert "src/parser.rs +18 -6" in text
        assert "3 tools" in text and "1 failed" in text


# -- colour ------------------------------------------------------------


class TestColour:
    def test_states_and_tool_families_carry_their_own_colour(self):
        raw = "".join(active_board().render(120, 34))
        palette = theme.current().swarm
        assert palette.working in raw  # the working group head and live card
        assert palette.fail in raw  # the failed call's duration
        assert tool_style("bash") in raw
        assert palette.people[0] in raw  # the first member's own hue

    def test_members_and_tool_families_do_not_share_one_hue(self):
        board = active_board()
        hues = {board._person(member.name) for member in board.view.members}
        assert len(hues) == len(board.view.members)
        assert len({tool_style(name) for name in ("bash", "read", "edit", "grep")}) == 4

    def test_no_colour_strips_the_whole_board(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        theme.apply_theme(theme.FX_DARK)
        try:
            raw = "".join(active_board().render(120, 34))
            assert "38;5;" not in raw
            assert "\x1b[0m" in raw  # rows still reset, so layout is unchanged
        finally:
            monkeypatch.delenv("NO_COLOR", raising=False)
            theme.apply_theme(theme.FX_DARK)
