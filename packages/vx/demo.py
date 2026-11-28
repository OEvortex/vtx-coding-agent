"""The swarm board, live: five agents working one refactor, drawn as a room.

Run it from this directory:

    cd packages/vx && uv run python demo.py

Nothing here calls a model. The room is simulated — the point of the demo is the
page it drives, :class:`vx.tui.swarm.SwarmBoard`, and the thing the page exists
to show: **what every agent is doing right now.** Each card carries the tool call
its worker is running, the spinner, and how long it has held. The right column
is the room's own history: every tool call with its duration and result,
interleaved with the posts on the board.

The simulation follows a small scripted story (lexer passes, #2 fails on `1n`,
the verifier pushes back, docs follow the grammar, bench watches the numbers),
including one worker that stops to ask you before it runs `rm -rf target`. Answer
it with ``1``/``2``/``3`` and the room moves on: approve and the call runs, deny
and that worker posts why it is skipping.

Keys: ``↑``/``↓`` select a member · ``⏎`` open its panel · ``esc`` back (then
quit) · ``tab`` roster/activity · ``v`` activity ⇄ board posts · ``1``-``9``
channel · ``f`` follow · ``space`` pause · ``+``/``-`` speed · ``?`` keys ·
``q`` quit.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field

from vx.tui import ansi, keys, theme
from vx.tui import swarm as board_page
from vx.tui.canvas import Painter
from vx.tui.swarm import Ask, EventView, MemberView, PostView, SwarmBoard, SwarmView
from vx.tui.terminal import Terminal

#: The demo redraws at a steady rate; the board's own animations are time based.
FPS = 20

#: Simulated wall-clock the room starts at, in seconds of the day.
START = 9 * 3600 + 41 * 60

#: A fixed origin for event timestamps, so the activity sparkline has a scale
#: without a real epoch behind it.
ORIGIN = 1_000_000.0

#: Tool calls each actor keeps: enough for its panel, not a transcript.
MAX_EVENTS = 40


@dataclass
class Beat:
    """One thing an actor does next: run a tool, say something, or park."""

    kind: str  # tool | say | wait | ask
    label: str = ""
    detail: str = ""
    secs: float = 1.0
    status: str = "ok"
    channel: str = ""


@dataclass
class Actor:
    """One simulated worker, and the little story it is in the middle of."""

    name: str
    role: str = "worker"
    model: str = "swe-2-flash"
    channels: tuple[str, ...] = ()
    workspace: str = "inherit"
    added_by: str = "coordinator"
    state: str = "working"
    beats: list[Beat] = field(default_factory=list)
    index: int = 0
    turns: int = 0
    tools: int = 0
    fails: int = 0
    events: list[EventView] = field(default_factory=list)
    current: EventView | None = None
    beat: Beat | None = None
    started_at: float = 0.0
    busy_until: float = 0.0
    last_read: int = 0
    last_text: str = ""
    blocked: bool = False
    read_at: float = 0.0
    read_seq: int = 0
    factor: float = 1.0

    @property
    def channel(self) -> str:
        return self.channels[0] if self.channels else "main"

    def activity(self, ask: Ask | None = None) -> str:
        if self.state == "killed":
            return "killed — its work needs a fresh worker"
        if ask is not None and ask.member == self.name:
            return f"needs you · {ask.label} {ask.target}"
        if self.current is not None:
            return f"{self.current.label} {self.current.detail}".strip()
        if self.state == "waiting":
            return self.last_text or "parked in board wait"
        return self.last_text or "working"


class Sim:
    """The room: a clock, a board, and the beats each actor is working through."""

    def __init__(self, *, speed: float = 1.0) -> None:
        self.speed = speed
        self.t = 0.0
        self._last = time.monotonic()
        self.seq = 0
        self.posts: list[PostView] = []
        self.actors = build_room()
        self.ask: Ask | None = None
        self.pending: tuple[Actor, Beat] | None = None
        self.answered = ""

    # -- view --------------------------------------------------------------

    def view(self) -> SwarmView:
        members = []
        for actor in self.actors:
            events = list(actor.events)
            if actor.current is not None:
                events.append(actor.current)
            members.append(
                MemberView(
                    name=actor.name,
                    role=actor.role,
                    model=actor.model,
                    state=actor.state,
                    channels=actor.channels,
                    workspace=actor.workspace,
                    turns=actor.turns,
                    age=int(self.t // 60),
                    last_read=actor.last_read,
                    added_by=actor.added_by,
                    activity=actor.activity(self.ask),
                    events=tuple(events),
                    tools=actor.tools,
                    fails=actor.fails,
                )
            )
        return SwarmView(
            title="the parser rewrite",
            members=members,
            posts=list(self.posts),
            channels={
                "parser": "lexer and parser rewrite",
                "docs": "user-facing documentation",
                "bench": "performance numbers",
            },
            dir="~/.config/ctx/swarms/9f2c-parser",
            elapsed=self.t,
        )

    @property
    def tools(self) -> int:
        return sum(actor.tools for actor in self.actors)

    # -- clock -------------------------------------------------------------

    def clock(self, at: float | None = None) -> str:
        total = int(START + (self.t if at is None else at))
        hour, rest = divmod(total, 3600)
        minute, second = divmod(rest, 60)
        return f"{hour % 24:02d}:{minute:02d}:{second:02d}"

    def advance(self, now: float, *, paused: bool) -> None:
        elapsed = min(0.25, max(0.0, now - self._last))
        self._last = now
        if paused:
            return
        self.t += elapsed * self.speed
        self._step()

    # -- the room ----------------------------------------------------------

    def _step(self) -> None:
        for actor in self.actors:
            if actor.read_at and self.t >= actor.read_at:
                actor.last_read = max(actor.last_read, actor.read_seq)
                actor.read_at = 0.0
            if actor.state == "killed" or actor.blocked:
                continue
            if actor.current is not None:
                actor.current.secs = max(0.0, self.t - actor.started_at)
                if self.t >= actor.busy_until:
                    self._finish(actor)
                continue
            if self.t >= actor.busy_until:
                self._start(actor)

    def _start(self, actor: Actor) -> None:
        beat = actor.beats[actor.index % len(actor.beats)]
        actor.index += 1
        if beat.kind == "wait":
            actor.state = "waiting"
            actor.last_text = beat.detail or f"parked in board wait · since #{self.seq}"
            actor.busy_until = self.t + beat.secs
            return
        if beat.kind == "say":
            actor.state = "working"
            self.post(actor, beat.label, beat.channel)
            actor.turns += 1
            actor.busy_until = self.t + max(0.25, beat.secs)
            return
        if beat.kind == "ask":
            # A guarded call: the worker stops and waits for a human.
            actor.state = "blocked"
            actor.blocked = True
            actor.beat = beat
            self.pending = (actor, beat)
            self.ask = Ask(member=actor.name, label=beat.label, target=beat.detail)
            return
        actor.state = "working"
        actor.beat = beat
        actor.started_at = self.t
        actor.current = EventView(
            agent=actor.name,
            kind="tool",
            label=beat.label,
            detail=beat.detail,
            status="run",
            clock=self.clock(),
            at=ORIGIN + self.t,
            secs=0.0,
            channel=beat.channel or actor.channel,
        )
        actor.busy_until = self.t + beat.secs * actor.factor

    def _finish(self, actor: Actor) -> None:
        beat = actor.beat
        call = actor.current
        if beat is None or call is None:
            return
        call.status = beat.status
        call.secs = max(0.05, self.t - actor.started_at)
        actor.events.append(call)
        del actor.events[:-MAX_EVENTS]
        actor.current = None
        actor.beat = None
        actor.tools += 1
        if beat.status == "fail":
            actor.fails += 1
        actor.busy_until = self.t + 0.15

    def resolve(self, choice: str) -> None:
        """Answer the pending ask: run the tool, or skip it and say why."""
        self.ask = None
        self.answered = choice
        if self.pending is None:
            return
        actor, beat = self.pending
        self.pending = None
        actor.blocked = False
        actor.beat = None
        actor.state = "working"
        if choice in ("approve", "allow"):
            actor.started_at = self.t
            actor.current = EventView(
                agent=actor.name,
                kind="tool",
                label=beat.label,
                detail=beat.detail,
                status="run",
                clock=self.clock(),
                at=ORIGIN + self.t,
                secs=0.0,
                channel=beat.channel or actor.channel,
            )
            actor.beat = beat
            actor.busy_until = self.t + beat.secs
            return
        actor.events.append(
            EventView(
                agent=actor.name,
                kind="tool",
                label=beat.label,
                detail=beat.detail,
                status="fail",
                clock=self.clock(),
                at=ORIGIN + self.t,
                secs=0.0,
                channel=beat.channel or actor.channel,
            )
        )
        actor.fails += 1
        self.post(actor, "@coordinator denied the clean rebuild — using the cached report instead")
        actor.busy_until = self.t + 0.6

    def post(self, actor: Actor, text: str, channel: str = "") -> None:
        self.seq += 1
        where = channel or actor.channel
        self.posts.append(PostView(self.seq, actor.name, text, where, self.clock()))
        actor.last_read = self.seq
        actor.last_text = text
        members = text.replace("@all", "@ " + " ".join(a.name for a in self.actors))
        for other in self.actors:
            if other.name in members or other.state == "waiting":
                other.last_read = self.seq
            else:
                other.read_at = self.t + 1.4
                other.read_seq = self.seq


# -- the story -----------------------------------------------------------


def tool(label: str, detail: str, secs: float = 1.0, status: str = "ok") -> Beat:
    return Beat("tool", label, detail, secs, status)


def say(text: str, channel: str = "") -> Beat:
    return Beat("say", text, channel=channel, secs=0.3)


def wait(secs: float, reason: str = "") -> Beat:
    return Beat("wait", detail=reason, secs=secs)


def ask(label: str, detail: str, secs: float = 2.0) -> Beat:
    return Beat("ask", label, detail, secs)


def build_room() -> list[Actor]:
    parser = Actor(
        name="parser",
        model="swe-2-flash",
        channels=("parser",),
        workspace="share",
        beats=[
            tool("bash", "cargo test -p parser --quiet", 1.6),
            tool("grep", "fn parse_number across src/", 0.5),
            tool("read", "src/parser.rs  L112-240", 0.7),
            tool("edit", "src/parser.rs  +18 −6", 1.2),
            tool("bash", "cargo test -p parser --quiet", 1.8),
            say("@verifier lexer passes on #2 — the number suffix is yours"),
            wait(5.0, "parked in board wait · since #2"),
            tool("bash", "cargo clippy -p parser --all-targets", 2.0),
            tool("edit", "src/parser.rs  +4 −1", 0.6),
            say("@verifier clippy is clean, take another look"),
            wait(4.5, "parked in board wait"),
            tool("bash", "cargo test --workspace --quiet", 3.0, "fail"),
            tool("read", "target/debug/test.log  L1-40", 0.5),
            say("@verifier workspace run trips in benches/lexer.rs — not mine, heads up"),
            wait(6.0, "parked in board wait"),
        ],
    )
    verifier = Actor(
        name="verifier",
        role="verifier",
        model="swe-2-flash",
        channels=("parser",),
        added_by="parser",
        beats=[
            tool("read", "tests/parser_cases.rs  L1-90", 0.8),
            tool("bash", "cargo test --test parser_cases", 2.2, "fail"),
            say("@parser #2 fails on `1n` — the lexer eats the suffix"),
            wait(6.0, "parked in board wait"),
            tool("read", "src/lexer.rs  L60-140", 0.6),
            tool("bash", "cargo test --test parser_cases", 1.7),
            say("@parser #2 is green on both branches"),
            wait(4.0, "parked in board wait"),
            tool("grep", "unsafe in src/parser.rs", 0.5),
            say("@parser no new unsafe in the diff — signing off"),
            wait(5.0, "parked in board wait"),
        ],
    )
    docs = Actor(
        name="docs",
        model="swe-2-mini",
        channels=("docs",),
        workspace="branch",
        beats=[
            tool("grep", "pub fn parse_ across src/", 0.6),
            tool("read", "docs/grammar.md  L1-120", 0.7),
            tool("write", "docs/grammar.md  +9 −2", 0.9),
            say("grammar doc updated for the numeric suffix", "docs"),
            wait(4.0, "parked in board wait"),
            tool("bash", "uv run mkdocs build --strict", 2.4),
            tool("edit", "docs/grammar.md  +2 −2", 0.5),
            say("docs build is clean", "docs"),
            wait(6.0, "parked in board wait"),
        ],
    )
    bench = Actor(
        name="bench",
        model="swe-2-mini",
        channels=("bench",),
        beats=[
            tool("bash", "cargo bench --bench lexer -- --quick", 2.8),
            tool("read", "target/criterion/lexer/new/estimates.json", 0.4),
            say("lexer 1.42 µs/op on 4 KB, no regression vs main", "bench"),
            wait(3.0, "parked in board wait"),
            ask("bash", "rm -rf target && cargo bench --bench lexer", 3.2),
            tool("read", "target/criterion/report/index.html", 0.5),
            say("clean rebuild: lexer 1.39 µs/op, inside the noise band", "bench"),
            wait(5.0, "parked in board wait"),
        ],
    )
    dead = Actor(
        name="old-worker",
        state="killed",
        model="swe-2-flash",
        workspace="share",
        last_text="killed — its work needs a fresh worker",
    )
    return [parser, verifier, docs, bench, dead]


# -- the page ------------------------------------------------------------


class Demo:
    """The board, plus the two things a demo needs: a clock and a speed."""

    def __init__(self, sim: Sim) -> None:
        self.sim = sim
        self.board = SwarmBoard()

    def key(self, event: str) -> bool:
        """Handle one key; False means quit."""
        if event in ("q", "ctrl+c"):
            return False
        if event in ("+", "="):
            self.sim.speed = min(4.0, self.sim.speed * 2)
            return True
        if event in ("-", "_"):
            self.sim.speed = max(0.25, self.sim.speed / 2)
            return True
        action = self.board.key(event)
        if action == "close":
            return False
        if action in ("approve", "allow", "deny"):
            self.sim.resolve(action)
            self.board.ask = None
        return True

    def frame(self, rows: int, cols: int, now: float) -> list[str]:
        if self.sim.ask is not None and self.board.ask is None:
            self.board.ask = self.sim.ask
        view = self.sim.view()
        self.board.view = view
        self.board.now = now
        if self.board.follow:
            self.board.seen_head = view.head
        if rows <= board_page.MIN_ROWS + 1:
            # No room for a banner: the board needs the row more than the demo
            # does, and the board's own "too small" page is exactly 10 rows.
            return self.board.render(cols, rows)
        banner = self._banner(cols)
        body = self.board.render(cols, rows - 1)
        return [banner, *body][:rows]

    def _banner(self, cols: int) -> str:
        t = theme.current()
        left = (
            f"{t.swarm.accent}{ansi.BOLD}vx swarm{ansi.RESET}"
            f"{t.dim_style} demo · simulated room, no model calls{ansi.RESET}"
        )
        right = (
            f"{t.swarm.working}{self.sim.speed:g}×{ansi.RESET}"
            f"{t.dim_style} · {self.sim.tools} tools · q quits{ansi.RESET}"
        )
        if ansi.width(left) + ansi.width(right) + 2 > cols:
            return ansi.pad(ansi.clip(left, cols), cols)
        return ansi.pad(left, cols - ansi.width(right)) + right


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="the swarm board, with a simulated room")
    parser.add_argument("--speed", type=float, default=1.0, help="simulation speed (default 1.0)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    theme.apply_theme()
    demo = Demo(Sim(speed=max(0.1, args.speed)))
    decoder = keys.KeyDecoder()
    painter = Painter()

    with Terminal() as term:
        while True:
            rows, cols = term.size()
            now = time.monotonic()
            demo.sim.advance(now, paused=demo.board.paused)
            for event in decoder.feed(term.read_available()):
                if not demo.key(event):
                    return 0
            for event in decoder.flush():
                if not demo.key(event):
                    return 0
            if term.eof:
                return 0
            frame = demo.frame(rows, cols, now)
            term.write(painter.paint(frame, cols))
            time.sleep(1 / FPS)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
