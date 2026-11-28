"""The swarm board: the page ``left`` opens when there is a swarm to watch.

A swarm is a room, not a list of agents. This draws the room: one roster column
where every member is a card that says what it is doing *right now* — the tool
it is running, how long it has been running, how many calls it has made — and
one activity column where the room's own history scrolls past: every tool call
with its duration and result, interleaved with the posts on the board. Open a
member and its column becomes that member's own panel.

Live tool calls are the point. A card is not decoration: it answers "what is
this one doing" without opening anything, and the activity column answers "what
has this room done" without reading the transcript.

The page reads a :class:`SwarmView`, which :func:`snapshot_from_engine` builds
from the live :class:`vx.swarm.SwarmEngine`. Nothing here imports the agent
harness: this module is layout and keys, and stays testable without a model.
"""

from __future__ import annotations

import re
import time
import zlib
from dataclasses import dataclass, field
from typing import Any

from vx.tui import ansi, theme

#: Below this a board is a diagram of a board: it says what it needs instead.
MIN_COLS = 52
MIN_ROWS = 10

#: The roster column: wide enough for a name and its numbers, never a third of
#: the room. The activity column is where the words are.
ROSTER_MIN = 28
ROSTER_MAX = 44

#: The states, in the order the roster lists them, with the glyph and the word.
STATES: tuple[tuple[str, str, str], ...] = (
    ("blocked", "◆", "needs you"),
    ("working", "◈", "working"),
    ("waiting", "⏸", "waiting"),
    ("stopped", "○", "idle"),
    ("killed", "✕", "killed"),
)

STATE_ORDER = {name: index for index, (name, _glyph, _word) in enumerate(STATES)}
STATE_WORD = {name: word for name, _glyph, word in STATES}

#: Tool families: the prefix of the tool name, the glyph that names it in one
#: cell, and the palette slot that colours it. A page that showed the same dot
#: for `bash` and `write` would be a list of events, not a picture of the work.
TOOL_FAMILIES: tuple[tuple[str, str, str], ...] = (
    ("bash", "❯", "bash"),
    ("shell", "❯", "bash"),
    ("exec", "❯", "bash"),
    ("read", "◎", "read"),
    ("view", "◎", "read"),
    ("open", "◎", "read"),
    ("edit", "✎", "edit"),
    ("write", "✎", "edit"),
    ("patch", "✎", "edit"),
    ("apply", "✎", "edit"),
    ("create", "✎", "edit"),
    ("grep", "⌕", "search"),
    ("search", "⌕", "search"),
    ("glob", "⌕", "search"),
    ("find", "⌕", "search"),
    ("list", "⌕", "search"),
    ("web", "☁", "web"),
    ("fetch", "☁", "web"),
    ("http", "☁", "web"),
    ("task", "⇄", "task"),
    ("delegate", "⇄", "task"),
    ("agent", "⇄", "task"),
    ("spawn", "⇄", "task"),
    ("board", "▤", "board"),
    ("swarm", "⬡", "board"),
    ("todo", "☑", "task"),
    ("plan", "☰", "task"),
    ("think", "✻", "tool"),
)

#: A running call spins. Time based, so it does not stutter when a frame runs long.
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

#: The header's activity sparkline, quietest to loudest.
BARS = "▁▂▃▄▅▆▇█"


def _family(name: str) -> tuple[str, str]:
    key = (name or "").strip().lower()
    for prefix, glyph, slot in TOOL_FAMILIES:
        if key == prefix or key.startswith(prefix):
            return glyph, slot
    return "⚙", "tool"


def tool_glyph(name: str) -> str:
    """The one cell that says which family of tool this call belongs to."""
    return _family(name)[0]


def tool_style(name: str) -> str:
    """That family's colour, so `bash` and `read` never look alike."""
    return getattr(theme.current().swarm, _family(name)[1])


def spinner(now: float) -> str:
    """A frame of the running-call spinner.

    Reduced motion holds the first cell: a spinner is information, not
    decoration, and a frozen one still says "this call is in flight".
    """
    index = 0 if theme.motion_reduced() else int(max(0.0, now) * 12) % len(SPINNER)
    return SPINNER[index]


def duration(secs: float) -> str:
    """A tool call's length in the fewest cells that still read as seconds."""
    secs = max(0.0, secs)
    if secs < 9.95:
        return f"{secs:.1f}s"
    if secs < 59.5:
        return f"{secs:.0f}s"
    minutes, seconds = divmod(round(secs), 60)
    return f"{minutes}m{seconds:02d}s"


def sparkline(values: list[int], width: int) -> str:
    """The last ``width`` buckets as bars: how busy the room has been.

    The scale has a floor: a room making a couple of calls a second should not
    paint a wall of full blocks, or the sparkline stops being a comparison.
    """
    tail = values[-width:] if width > 0 else []
    if not tail:
        return ""
    top = max(3, max(tail))
    out: list[str] = []
    for value in tail:
        if value <= 0:
            out.append("·")
        else:
            out.append(BARS[min(len(BARS) - 1, value * len(BARS) // top)])
    return "".join(out)


# ---------------------------------------------------------------------------
# colour: the board is a dashboard, so it says who and what in more than one hue
# ---------------------------------------------------------------------------


def state_style(state: str) -> str:
    """The colour a state's glyph and its group head carry."""
    palette = theme.current().swarm
    return {
        "working": palette.working,
        "blocked": palette.blocked,
        "waiting": palette.waiting,
        "stopped": palette.waiting,
        "killed": palette.killed,
    }.get(state, palette.waiting)


def person_style(name: str, index: int | None = None) -> str:
    """A member's own colour, fixed by its name so it never moves between frames.

    ``index`` is the member's roster position when the caller knows it: workers
    are then coloured in turn, so two of them never land on the same hue. Names
    without one (a coordinator post, an unknown agent) hash instead, which is
    stable but can repeat — a repeated colour is a small price for a stable page.
    """
    people = theme.current().swarm.people
    if not people:
        return ""
    slot = zlib.crc32((name or "").encode()) if index is None else index
    return people[slot % len(people)]


# ---------------------------------------------------------------------------
# what the page draws: a snapshot of the swarm, taken by the app each frame
# ---------------------------------------------------------------------------


@dataclass
class EventView:
    """One thing a member did: a tool call, or a post on the board.

    ``secs`` is the call's duration once it finishes, and the elapsed time while
    it is still running — so a running call reads ``⚠ bash 2.4s`` and ticks.
    """

    agent: str = ""
    kind: str = "tool"  # tool | say
    label: str = ""
    detail: str = ""
    status: str = "ok"  # run | ok | fail
    clock: str = ""
    at: float = 0.0
    secs: float = 0.0
    channel: str = "main"
    seq: int = 0

    @property
    def running(self) -> bool:
        return self.status == "run"


@dataclass
class MemberView:
    """One agent in the room, as the board draws it."""

    name: str
    role: str = "worker"
    model: str = ""
    state: str = "working"  # working | waiting | stopped | killed | blocked
    channels: tuple[str, ...] = ()
    workspace: str = "inherit"
    turns: int = 0
    age: int = 0
    last_read: int = 0
    added_by: str = "coordinator"
    activity: str = ""
    note: str = ""
    pending_model: str = ""
    #: Newest last: the tool calls this member has made, plus the running one.
    events: tuple[EventView, ...] = ()
    tools: int = 0
    fails: int = 0

    @property
    def channel(self) -> str:
        return self.channels[0] if self.channels else "main"

    @property
    def running(self) -> EventView | None:
        return next((event for event in reversed(self.events) if event.running), None)


@dataclass
class PostView:
    """One thing said on the board."""

    seq: int
    agent: str
    text: str
    channel: str = "main"
    at: str = ""


@dataclass
class Ask:
    """A worker blocked on you: what it wants to run, and the three answers."""

    member: str
    label: str = ""
    target: str = ""
    selected: int = 0

    @property
    def options(self) -> tuple[str, str, str]:
        name = self.label or "it"
        if ansi.width(f"allow {name} for this swarm") <= 34:
            return ("approve once", f"allow {name} for this swarm", "deny")
        return ("approve", "allow always", "deny")

    @property
    def choice(self) -> str:
        """The decision this selection means: ``approve`` / ``allow`` / ``deny``."""
        return ("approve", "allow", "deny")[max(0, min(2, self.selected))]

    def target_line(self, width: int) -> str:
        return ansi.clip_ellipsis(self.target or self.label, max(8, width))


@dataclass
class SwarmView:
    """The whole room, or an empty one when this conversation has no swarm."""

    title: str = ""
    members: list[MemberView] = field(default_factory=list)
    posts: list[PostView] = field(default_factory=list)
    channels: dict[str, str] = field(default_factory=dict)
    dir: str = ""
    #: How long the swarm has been up, in seconds, as of the snapshot.
    elapsed: float = 0.0

    @property
    def exists(self) -> bool:
        return bool(self.title or self.members or self.posts)

    @property
    def head(self) -> int:
        return self.posts[-1].seq if self.posts else 0

    def member(self, name: str) -> MemberView | None:
        return next((m for m in self.members if m.name == name), None)

    def counts(self) -> dict[str, int]:
        out = {name: 0 for name, _glyph, _word in STATES}
        for member in self.members:
            out[member.state] = out.get(member.state, 0) + 1
        return out

    def unread(self, member: MemberView) -> int:
        return max(0, self.head - member.last_read)

    def posts_by(self, name: str) -> list[PostView]:
        return [post for post in self.posts if post.agent == name]

    def ordered(self) -> list[MemberView]:
        """Working first, then waiting, idle, killed; helpers under their author."""
        depths: dict[str, int] = {}

        def depth_of(member: MemberView, seen: frozenset[str] = frozenset()) -> int:
            if member.name in depths:
                return depths[member.name]
            parent = member.added_by
            value = 0
            if parent and parent != "coordinator" and parent not in seen:
                upstream = self.member(parent)
                if upstream is not None:
                    value = 1 + depth_of(upstream, seen | {member.name})
            depths[member.name] = value
            return value

        return sorted(
            self.members, key=lambda m: (STATE_ORDER.get(m.state, 9), depth_of(m), m.name)
        )

    # -- the timeline ------------------------------------------------------

    def events(self) -> list[EventView]:
        """Tool calls and posts, oldest first: the room's own history."""
        out: list[EventView] = []
        for member in self.members:
            out.extend(member.events)
        out.extend(
            EventView(
                agent=post.agent,
                kind="say",
                label="board",
                detail=post.text,
                channel=post.channel,
                clock=post.at,
                seq=post.seq,
            )
            for post in self.posts
        )
        return sorted(out, key=_event_order)

    def timeline(self, channel: str = "all") -> list[EventView]:
        events = self.events()
        if channel not in ("all", ""):
            events = [event for event in events if event.channel == channel]
        return events

    @property
    def tool_count(self) -> int:
        return sum(member.tools for member in self.members)

    @property
    def fail_count(self) -> int:
        return sum(member.fails for member in self.members)

    def series(self, buckets: int = 26, span: float = 60.0) -> list[int]:
        """Tool calls per bucket over the last ``span`` seconds."""
        marks = [
            event.at
            for member in self.members
            for event in member.events
            if event.kind == "tool" and event.at > 0
        ]
        if not marks:
            return []
        newest = max(marks)
        out = [0] * buckets
        width = span / max(1, buckets)
        for at in marks:
            age = newest - at
            if 0 <= age < span:
                index = buckets - 1 - int(age // width)
                out[max(0, min(buckets - 1, index))] += 1
        return out

    def rate(self, span: float = 60.0) -> float:
        """Tool calls per minute over the last ``span`` seconds."""
        marks = [
            event.at
            for member in self.members
            for event in member.events
            if event.kind == "tool" and event.at > 0
        ]
        if not marks:
            return 0.0
        newest = max(marks)
        recent = sum(1 for at in marks if 0 <= newest - at < span)
        return recent * 60.0 / span


def _clock_seconds(clock: str) -> float:
    """``HH:MM:SS`` as seconds of the day; unreadable clocks sort first."""
    parts = (clock or "").split(":")
    try:
        numbers = [int(part) for part in parts if part.strip()]
    except ValueError:
        return -1.0
    while len(numbers) < 3:
        numbers.insert(0, 0)
    hour, minute, second = numbers[-3:]
    return hour * 3600 + minute * 60 + second


def _event_order(event: EventView) -> tuple[float, int]:
    return (_clock_seconds(event.clock), event.seq)


def _activity_of(member: Any, *, now: float | None = None) -> str:
    """What a live member is doing, from the little the engine knows for certain.

    The running tool call wins: it is the one thing a watcher wants, and it is
    true while it lasts. Otherwise the last thing the member said stands in.
    """
    if getattr(member.row, "killed", False) or member.state == "killed":
        return "killed — its work needs a fresh worker"
    awaiting = getattr(member, "awaiting", "")
    if awaiting:
        return f"needs you · {awaiting}"
    current = getattr(member, "current", None)
    if current is not None:
        label = getattr(current, "label", "") or "tool"
        detail = " ".join(str(getattr(current, "detail", "") or "").split())
        return f"{label} {detail}".strip()
    if member.state == "waiting":
        return f"parked in board wait · since #{member.row.last_read_seq}"
    if member.state == "stopped":
        return "between turns · a post naming it wakes it"
    if getattr(member, "pending_model", ""):
        return f"switching to {member.pending_model} at its next turn"
    stream = " ".join((getattr(member, "stream", "") or "").split())
    text = stream or " ".join((getattr(member, "last_text", "") or "").split())
    if text:
        return text[:160]
    return "working"


def _event_from_activity(event: Any, *, now: float) -> EventView:
    """One engine-side activity record, as the page draws it."""
    started = float(getattr(event, "at", 0.0) or 0.0)
    status = str(getattr(event, "status", "ok") or "ok")
    took = float(getattr(event, "took", 0.0) or 0.0)
    if status == "run" and started:
        took = max(0.0, now - started)
    return EventView(
        agent=str(getattr(event, "agent", "") or ""),
        kind=str(getattr(event, "kind", "tool") or "tool"),
        label=str(getattr(event, "label", "") or ""),
        detail=" ".join(str(getattr(event, "detail", "") or "").split())[:160],
        status=status,
        clock=time.strftime("%H:%M:%S", time.localtime(started)) if started else "",
        at=started,
        secs=took,
        channel=str(getattr(event, "channel", "main") or "main"),
    )


def snapshot_from_engine(engine: Any, *, now: float | None = None) -> SwarmView:
    """Read a live :class:`vx.swarm.SwarmEngine` into the page's own shape."""
    if engine is None:
        return SwarmView()
    now = time.time() if now is None else now
    board = engine.board
    members: list[MemberView] = []
    for name, live in engine.members.items():
        row = live.row
        started = getattr(live, "started_at", 0.0) or 0.0
        channel = row.channels[0] if row.channels else "main"
        events = [_event_from_activity(event, now=now) for event in getattr(live, "activity", ())]
        current = getattr(live, "current", None)
        if current is not None:
            running = _event_from_activity(current, now=now)
            running.agent = name
            running.channel = channel
            events.append(running)
        for event in events:
            event.agent = event.agent or name
        members.append(
            MemberView(
                name=name,
                role=row.role,
                model=row.model or "",
                state=(
                    "killed"
                    if row.killed
                    else ("blocked" if getattr(live, "awaiting", "") else live.state)
                ),
                channels=tuple(row.channels),
                workspace=row.workspace,
                turns=getattr(live, "turns", 0),
                age=int(max(0.0, now - started) // 60) if started else 0,
                last_read=row.last_read_seq,
                added_by=row.added_by or "coordinator",
                activity=_activity_of(live, now=now),
                note=row.note,
                pending_model=getattr(live, "pending_model", ""),
                events=tuple(events),
                tools=int(getattr(live, "tools", 0) or 0),
                fails=int(getattr(live, "fails", 0) or 0),
            )
        )
    posts = [
        PostView(
            post.seq,
            post.agent,
            post.text,
            post.channel,
            post.time[-8:] if len(post.time) >= 8 else post.time[-5:],
        )
        for post in board.since(0)
    ]
    return SwarmView(
        title=engine.board.title or "swarm",
        members=members,
        posts=posts,
        channels={
            name: (getattr(spec, "topic", "") or str(spec or ""))
            for name, spec in board.channels.items()
        },
        dir=str(engine.dir),
        elapsed=max(0.0, now - float(getattr(engine, "created_at", 0.0) or now)),
    )


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------


@dataclass
class SwarmBoard:
    """Header, channels, roster, activity column, and any member's own panel.

    Every band is budgeted before it is drawn, so a short terminal loses
    activity rows before it loses the roster, and loses both before it loses the
    header. A frame is exactly ``height`` rows of exactly ``width`` cells.
    """

    view: SwarmView = field(default_factory=SwarmView)
    selected: int = 0
    focus: str = "roster"  # roster | feed
    channel: str = "all"
    follow: bool = True
    scroll: int = 0
    open_member: str = ""
    panel: str = "activity"  # activity | board
    help: bool = False
    paused: bool = False
    now: float = 0.0
    seen_head: int = 0
    #: The worker's pending tool call, when one is waiting on a human.
    ask: Ask | None = None

    # -- page --------------------------------------------------------------

    def render(self, width: int, height: int) -> list[str]:
        if width < MIN_COLS or height < MIN_ROWS:
            rows = self._too_small(width, height)
        elif not self.view.exists:
            rows = self._empty(width, height)
        else:
            rows = [self._header(width), self._rule(width), self._strip(width), self._rule(width)]
            band = self._ask_band(width) if self.ask is not None else []
            body_h = max(0, height - len(rows) - 1 - len(band))
            rows.extend(self._body(width, body_h))
            rows.extend(band)
            rows.append(self._footer(width))
        rows = self._fit(rows, height)
        # The key box lands on every page, including "no swarm yet": the keys work
        # there too, and asking what they are should always answer.
        if self.help:
            rows = self._overlay_help(rows, width, height)
        return [ansi.pad(ansi.clip(row, width), width) for row in rows]

    def _too_small(self, width: int, height: int) -> list[str]:
        return self._centred(
            [
                f"{theme.current().swarm.accent}⬡ vx swarm{ansi.RESET}",
                "",
                f"{theme.current().dim_style}this board needs {MIN_COLS}x{MIN_ROWS}{ansi.RESET}",
                f"{theme.current().dim_style}the terminal is "
                f"{max(0, width)}x{max(0, height)}{ansi.RESET}",
            ],
            width,
            height,
        )

    def _empty(self, width: int, height: int) -> list[str]:
        t = theme.current()
        return self._centred(
            [
                f"{t.swarm.accent}⬡{ansi.RESET} {t.swarm.accent}{ansi.BOLD}vx swarm{ansi.RESET}",
                "",
                f"{t.hint_style}no swarm in this conversation{ansi.RESET}",
                "",
                f"{t.dim_style}ask for one in the composer: “swarm this refactor{ansi.RESET}",
                f"{t.dim_style}across three files” — workers show up here as they start."
                f"{ansi.RESET}",
                "",
                f"{t.dim_style}esc or left goes back{ansi.RESET}",
            ],
            width,
            height,
        )

    def _centred(self, lines: list[str], width: int, height: int) -> list[str]:
        top = max(0, (height - len(lines)) // 2)
        out = self._fit([""] * top + lines, height)
        centred = []
        for row in out:
            left = max(0, (width - ansi.width(row)) // 2)
            centred.append(" " * left + row)
        return [ansi.pad(ansi.clip(row, width), width) for row in centred]

    # -- bands -------------------------------------------------------------

    def _header(self, width: int) -> str:
        t = theme.current()
        counts = self.view.counts()
        left = (
            f"{t.swarm.accent}⬡{ansi.RESET} "
            f"{t.swarm.accent}{ansi.BOLD}{self.view.title}{ansi.RESET}"
        )
        head = f"{t.swarm.clock}board #{self.view.head}{ansi.RESET}"
        chips: list[str] = []
        for name, glyph, word in STATES:
            count = counts.get(name, 0)
            if not count:
                continue
            style = state_style(name)
            chips.append(f"{style}{glyph}{ansi.RESET}{t.dim_style} {count} {word}{ansi.RESET}")
        joined = f"{t.dim_style}   {ansi.RESET}".join(chips)
        stamp = (
            f"{t.swarm.clock}{duration(self.view.elapsed)}{ansi.RESET}"
            if self.view.elapsed >= 1
            else ""
        )
        tally = f"{t.dim_style}   {ansi.RESET}".join(part for part in (stamp, joined) if part)
        right = head
        for candidate in (
            f"{tally}   {head}" if tally else "",
            f"{t.dim_style}{len(self.view.members)} agents{ansi.RESET}   {head}",
            head,
        ):
            if candidate and ansi.width(left) + ansi.width(candidate) + 2 <= width:
                right = candidate
                break
        return ansi.clip(ansi.pad(left, max(1, width - ansi.width(right))) + right, width)

    def _rule(self, width: int) -> str:
        t = theme.current()
        return f"{t.divider_style}{'─' * max(0, width)}{ansi.RESET}"

    def _strip(self, width: int) -> str:
        """Channel chips on the left; how busy the room is on the right."""
        t = theme.current()
        parts = [self._channel_chip("all"), self._channel_chip("main")]
        parts.extend(self._channel_chip(name) for name in self.view.channels)
        left = " " + f"{t.dim_style}   {ansi.RESET}".join(parts)

        state = (
            f"{t.swarm.blocked}paused{ansi.RESET}"
            if self.paused
            else (
                f"{t.swarm.working}● live{ansi.RESET}"
                if self.follow
                else f"{t.swarm.waiting}‖ held{ansi.RESET}"
            )
        )
        fresh = 0 if self.follow else max(0, self.view.head - self.seen_head)
        if fresh:
            state += f"{t.swarm.blocked}  ▲ {fresh} new{ansi.RESET}"
        tools = self.view.tool_count
        rate = self.view.rate()
        segments = [state]
        if tools:
            segments.insert(0, f"{t.dim_style}{tools} tools{ansi.RESET}")
        if tools and rate:
            segments.insert(1, f"{t.dim_style}{rate:.1f}/min{ansi.RESET}")
        marks = sparkline(self.view.series(26), 26)
        if marks:
            # Quiet buckets stay grey so the bars that move are the ones you see.
            lit = "".join(
                f"{t.swarm.clock}·" if ch == "·" else f"{t.swarm.working}{ch}" for ch in marks
            )
            segments.insert(0, lit + ansi.RESET)
        right = ""
        for segment in reversed(segments):
            candidate = segment if not right else f"{segment}{t.dim_style}   {ansi.RESET}{right}"
            if ansi.width(left) + ansi.width(candidate) + 1 <= width:
                right = candidate
        row = left
        if right and ansi.width(row) + ansi.width(right) + 1 <= width:
            row = ansi.pad(row, width - ansi.width(right)) + right
        return ansi.clip(row, width)

    def _channel_chip(self, name: str) -> str:
        t = theme.current()
        label = f"#{name}"
        count = 0
        if name not in ("all", "main"):
            count = sum(1 for m in self.view.members if name in m.channels)
        text = f"{label}{f' {count}' if count else ''}"
        if self.channel == name:
            return f"{t.swarm.accent}{ansi.BOLD}{text}{ansi.RESET}"
        return f"{t.dim_style}{text}{ansi.RESET}"

    def _footer(self, width: int) -> str:
        t = theme.current()
        if self.ask is not None:
            keys = "1-3 answer · ↑↓ choose · enter confirm · esc deny"
        elif self.open_member:
            keys = "esc back · ↑↓ member · tab roster · left close"
        elif self.focus == "feed":
            keys = "tab roster · ↑↓ scroll · f follow · v panel · ⏎ member · ? keys"
        else:
            keys = "↑↓ select · ⏎ member · tab feed · v panel · 1-9 channel · ? keys · left close"
        hint = f"{t.dim_style}{ansi.clip_ellipsis(keys, width)}{ansi.RESET}"
        if self.open_member:
            tag = f"{self._person(self.open_member)}viewing @{self.open_member}{ansi.RESET}"
            if ansi.width(tag) + 2 < width:
                return ansi.pad(tag, width - ansi.width(hint)) + hint
        return ansi.pad(hint, width)

    def _person(self, name: str) -> str:
        """A member's colour by roster position: the room reads as a set of people."""
        order = [member.name for member in self.view.members]
        index = order.index(name) if name in order else None
        return person_style(name, index)

    # -- the body ----------------------------------------------------------

    def _body(self, width: int, height: int) -> list[str]:
        if height <= 0:
            return []
        if width >= 84:
            roster_w = max(ROSTER_MIN, min(ROSTER_MAX, width * 36 // 100))
            feed_w = max(20, width - roster_w - 3)
            left = self._roster(roster_w, height)
            right = self._right(feed_w, height)
            rule = f"{theme.current().divider_style}│{ansi.RESET}"
            return [
                ansi.pad(left[index] if index < len(left) else "", roster_w)
                + f" {rule} "
                + (right[index] if index < len(right) else "")
                for index in range(height)
            ]
        members = len(self.view.members)
        roster_h = min(members * 3, max(2, height // 2))
        compact = roster_h < members * 2
        roster = self._roster(width, roster_h, compact=compact)
        rest = height - len(roster) - (1 if height - len(roster) > 2 else 0)
        rows = list(roster)
        if rest > 0:
            rows.append(self._rule(width))
        rows.extend(self._right(width, max(0, rest)))
        return rows[:height]

    def _right(self, width: int, height: int) -> list[str]:
        if self.open_member:
            return self._member_panel(self.open_member, width, height)
        return self._activity(width, height)

    # -- needs you ---------------------------------------------------------

    def _ask_band(self, width: int) -> list[str]:
        """The permission request, on the page rather than in the transcript.

        A worker waiting for a human is the one thing on a live board that cannot
        wait, so it gets the last rows above the keys and the keys themselves.
        """
        ask = self.ask
        if ask is None:
            return []
        t = theme.current()
        name = ask.member
        who = self._person(name)
        rows = [
            self._rule(width),
            ansi.clip(
                f" {t.swarm.blocked}◆{ansi.RESET} {who}{ansi.BOLD}@{name}{ansi.RESET}"
                f"{t.hint_style} wants to run "
                f"{tool_style(ask.label)}{ansi.BOLD}{ask.label}{ansi.DIM_OFF}{ansi.RESET}",
                width,
            ),
            ansi.clip(f" {t.dim_style}$ {ask.target_line(width - 4)}{ansi.RESET}", width),
        ]
        chips: list[str] = []
        for index, option in enumerate(ask.options):
            label = f"{index + 1} {option}"
            if index == ask.selected:
                chips.append(f"{t.swarm.accent}{ansi.BOLD}▸ {label}{ansi.RESET}")
            else:
                chips.append(f"{t.dim_style}  {label}{ansi.RESET}")
        line = " " + f"{t.dim_style}   {ansi.RESET}".join(chips)
        rows.append(ansi.clip(line, width))
        return rows

    # -- roster ------------------------------------------------------------

    def _roster(self, width: int, height: int, *, compact: bool = False) -> list[str]:
        t = theme.current()
        members = self.view.ordered()
        if not members:
            return [f"{t.dim_style}  no workers yet{ansi.RESET}"]
        grouped = not compact and height >= len(members) * 3 + 4
        lines: list[str] = []
        last_state = ""
        for position, member in enumerate(members):
            card = self._card(member, width, position == self.selected, compact=compact)
            addition: list[str] = []
            opens_group = grouped and member.state != last_state
            if opens_group:
                if lines:
                    addition.append("")
                addition.append(self._group_head(member.state, members))
            elif lines and not compact:
                addition.append("")
            addition.extend(card)
            if len(lines) + len(addition) > height:
                remaining = len(members) - position
                marker = f"{t.dim_style}  +{remaining} more…{ansi.RESET}"
                if height - len(lines) >= 1:
                    lines.append(marker)
                elif len(lines) > 1:
                    lines[-1] = marker
                break
            lines.extend(addition)
            if opens_group:
                last_state = member.state
        return lines[:height]

    def _group_head(self, state: str, members: list[MemberView]) -> str:
        count = sum(1 for member in members if member.state == state)
        word = STATE_WORD.get(state, state)
        return f"{state_style(state)}{word.upper()} {count}{ansi.RESET}"

    def _card(self, member: MemberView, width: int, chosen: bool, *, compact: bool) -> list[str]:
        t = theme.current()
        indent = "  " * self._depth(member)
        glyph, style = self._state_face(member)
        bar = f"{t.swarm.accent}▌{ansi.RESET}" if chosen else " "
        lead = f"{bar}{indent}"
        person = self._person(member.name)
        name_style = f"{person}{ansi.BOLD}" if chosen else (person or t.hint_style)
        name = f"{name_style}{member.name}{ansi.RESET}"
        if member.role not in ("worker", "") and member.role != member.name:
            name += f"{t.dim_style} · {member.role}{ansi.RESET}"
        numbers = self._numbers(member)
        room = width - ansi.width(lead) - 3 - ansi.width(numbers) - 1
        first = f"{lead} {style}{glyph}{ansi.RESET} "
        first += ansi.clip_ellipsis(name, max(4, room))
        first = ansi.pad(first, max(1, width - ansi.width(numbers))) + numbers
        if compact:
            room_now = width - ansi.width(first) - 3
            if room_now >= 12:
                return [
                    ansi.clip(
                        f"{first}{t.dim_style} · "
                        f"{ansi.clip_ellipsis(member.activity, room_now)}{ansi.RESET}",
                        width,
                    )
                ]
            return [ansi.clip(first, width)]
        pad = f"{bar}{indent}   "
        counters = self._counters(member)
        where = ansi.clip_ellipsis(
            self._where_line(member), max(6, width - ansi.width(pad) - ansi.width(counters) - 1)
        )
        second = ansi.pad(
            f"{pad}{t.dim_style}{where}{ansi.RESET}", max(1, width - ansi.width(counters))
        )
        second += counters
        third = f"{pad}{self._now_line(member, max(8, width - ansi.width(pad)))}"
        return [ansi.clip(first, width), ansi.clip(second, width), ansi.clip(third, width)]

    def _numbers(self, member: MemberView) -> str:
        """Age and turns, right aligned: the two numbers a watcher skims for."""
        t = theme.current()
        unread = "" if member.state == "killed" else self.view.unread(member)
        tail = (
            f"{t.swarm.accent}+{unread}{ansi.RESET}" if unread else f"{t.swarm.clock}·{ansi.RESET}"
        )
        return f"{t.dim_style}{member.age}m {member.turns}t{ansi.RESET} {tail}"

    def _counters(self, member: MemberView) -> str:
        """How much work this member has actually done."""
        t = theme.current()
        if member.state == "killed":
            return f"{t.dim_style}✕{ansi.RESET} "
        out = f"{t.dim_style}{member.tools}⌁{ansi.RESET}"
        if member.fails:
            out += f"{t.swarm.fail} {member.fails}✗{ansi.RESET}"
        return f"{out} "

    def _now_line(self, member: MemberView, width: int) -> str:
        """The one line that answers "what is it doing": the tool it is running."""
        t = theme.current()
        running = member.running
        activity = member.activity or "—"
        if member.state in ("waiting", "stopped", "killed"):
            return f"{t.dim_style}{ansi.clip_ellipsis(activity, max(4, width))}{ansi.RESET}"
        if member.state == "blocked":
            blocked = t.swarm.blocked
            return f"{blocked}{ansi.clip_ellipsis(activity, max(4, width))}{ansi.RESET}"
        if running is None:
            return f"{t.statusline_style}{ansi.clip_ellipsis(activity, max(4, width))}{ansi.RESET}"
        head = f"{t.swarm.working}{spinner(self.now)}{ansi.RESET} "
        tail = duration(running.secs)
        room = max(4, width - ansi.width(head) - ansi.width(tail) - 1)
        label = ansi.clip_ellipsis(running.label or "tool", 12)
        lead = f"{tool_style(running.label)}{tool_glyph(running.label)}{ansi.RESET} "
        lead += f"{t.hint_style}{label}{ansi.RESET} "
        rest = max(0, room - ansi.width(lead))
        body = lead + f"{t.statusline_style}{ansi.clip_ellipsis(running.detail, rest)}{ansi.RESET}"
        row = f"{head}{body}"
        if tail and ansi.width(row) + ansi.width(tail) + 1 <= width:
            row = ansi.pad(row, width - ansi.width(tail)) + f"{t.swarm.clock}{tail}{ansi.RESET}"
        return row

    def _where_line(self, member: MemberView) -> str:
        bits = [member.model or "—"]
        if member.channels:
            bits.append(" ".join("#" + c for c in member.channels))
        elif member.role in ("narrator", "monitor"):
            bits.append("all channels")
        if member.workspace != "inherit":
            bits.append(member.workspace)
        return " · ".join(bits)

    def _depth(self, member: MemberView) -> int:
        depth = 0
        parent = member.added_by
        seen = {member.name}
        while parent and parent != "coordinator" and parent not in seen:
            upstream = self.view.member(parent)
            if upstream is None:
                break
            depth += 1
            seen.add(parent)
            parent = upstream.added_by
        return depth

    def _state_face(self, member: MemberView) -> tuple[str, str]:
        if member.state == "working":
            frames = ("◇", "◈", "◆", "◈")
            pulse = frames[0 if theme.motion_reduced() else int(self.now * 3) % 4]
            return pulse, state_style("working")
        glyphs = {"blocked": "◆", "waiting": "⏸", "stopped": "○", "killed": "✕"}
        return glyphs.get(member.state, "·"), state_style(member.state)

    # -- the activity column -----------------------------------------------

    def shown_events(self) -> list[EventView]:
        """What the activity column lists: tool calls, or only board posts."""
        events = self.view.timeline(self.channel)
        if self.panel == "board":
            return [event for event in events if event.kind == "say"]
        return events

    def _activity(self, width: int, height: int) -> list[str]:
        t = theme.current()
        if height <= 0:
            return []
        events = self.shown_events()
        rows: list[str] = []
        if height >= 3:
            rows.append(self._panel_head(width, len(events)))
            height -= 1
        if not events:
            rows.append(f"{t.dim_style}  nothing has happened yet{ansi.RESET}")
            return rows
        room = max(0, height)
        tail = events if self.follow else events[: max(0, len(events) - self.scroll)]
        shown = tail[-room:] if room else []
        if rows and len(shown) < len(tail):
            rows[-1] = self._panel_head(width, len(events), hidden=len(tail) - len(shown))
        body = [
            self._event_row(event, width, newest=index == len(shown) - 1)
            for index, event in enumerate(shown)
        ]
        pad = max(0, height - len(body))
        return rows + [""] * pad + body

    def _panel_head(self, width: int, count: int, *, hidden: int = 0) -> str:
        t = theme.current()
        label = "ACTIVITY · BOARD" if self.panel == "activity" else "BOARD"
        where = "all channels" if self.channel == "all" else f"#{self.channel}"
        left = (
            f"{t.swarm.accent}▾{ansi.RESET} {t.swarm.accent}{ansi.BOLD}{label}{ansi.DIM_OFF}"
            f"{t.dim_style} · {where}{ansi.RESET}"
        )
        if hidden:
            right = f"{t.dim_style}+{hidden} older{ansi.RESET}"
        elif self.channel != "all" and self.channel in self.view.channels:
            topic = self.view.channels[self.channel]
            right = f"{t.dim_style}{ansi.clip_ellipsis(topic, max(0, width // 2))}{ansi.RESET}"
        else:
            word = "entries" if self.panel == "activity" else "posts"
            right = f"{t.dim_style}{count} {word}{ansi.RESET}"
        if ansi.width(left) + ansi.width(right) + 1 <= width:
            return ansi.pad(left, width - ansi.width(right)) + right
        return ansi.clip(left, width)

    def _event_row(
        self, event: EventView, width: int, *, newest: bool = False, author: bool = True
    ) -> str:
        """One tool call or one post: who, what, how long, how it went."""
        t = theme.current()
        lead = ""
        if width >= 78 and event.clock:
            lead += f"{t.swarm.clock}{event.clock:>8}{ansi.RESET} "
        if author and width >= 64 and event.agent:
            who = ansi.clip_ellipsis("@" + event.agent, 10)
            lead += f"{self._person(event.agent)}{ansi.pad(who, 11)}{ansi.RESET}"
        if event.kind == "say":
            glyph, label = "▤", "board"
            glyph_style = t.swarm.board
        else:
            glyph = tool_glyph(event.label)
            label = event.label or "tool"
            glyph_style = tool_style(event.label)
        lead += (
            f"{glyph_style}{glyph}{ansi.RESET} "
            f"{t.hint_style}{ansi.pad(ansi.clip_ellipsis(label, 7), 7)}{ansi.RESET}"
        )
        tail = ""
        if event.kind == "say":
            if event.seq:
                tail = f"{t.swarm.clock}#{event.seq}{ansi.RESET}"
        else:
            if event.running:
                mark = f"{t.swarm.accent}{spinner(self.now)}{ansi.RESET}"
                took = f"{t.swarm.accent}{duration(event.secs):>5}{ansi.RESET}"
            elif event.status == "fail":
                mark = f"{t.swarm.fail}✗{ansi.RESET}"
                took = f"{t.swarm.fail}{duration(event.secs):>5}{ansi.RESET}"
            else:
                mark = f"{t.swarm.ok}✓{ansi.RESET}"
                took = f"{t.dim_style}{duration(event.secs):>5}{ansi.RESET}"
            tail = f"{took} {mark}"
        room = max(4, width - ansi.width(lead) - ansi.width(tail) - 1)
        text = _styled(event.detail) if event.kind == "say" else event.detail
        body_style = t.hint_style if newest else t.statusline_style
        body = f"{body_style}{ansi.clip_ellipsis(text, room)}{ansi.RESET}"
        row = f"{lead}{body}"
        if tail and ansi.width(row) + ansi.width(tail) + 1 <= width:
            row = ansi.pad(row, width - ansi.width(tail)) + tail
        return ansi.clip(row, width)

    # -- one member's panel ------------------------------------------------

    def _member_panel(self, name: str, width: int, height: int) -> list[str]:
        t = theme.current()
        member = self.view.member(name)
        if member is None:
            return [f"{t.dim_style}  no member {name!r}{ansi.RESET}"]
        glyph, style = self._state_face(member)
        unread = self.view.unread(member)
        head = (
            f"{t.swarm.accent}▾{ansi.RESET} {t.swarm.accent}{ansi.BOLD}MEMBER"
            f"{ansi.DIM_OFF}{ansi.RESET}"
        )
        where = " ".join("#" + c for c in member.channels) or "#main"
        if member.workspace != "inherit":
            where += f" · {member.workspace}"
        if ansi.width(head) + ansi.width(where) + 2 <= width:
            head = ansi.pad(head, width - ansi.width(where)) + f"{t.dim_style}{where}{ansi.RESET}"
        role = (
            ""
            if member.role in ("worker", "") or member.role == member.name
            else f"{t.dim_style} · {member.role}{ansi.RESET}"
        )
        rows = [
            ansi.clip(head, width),
            f" {style}{glyph}{ansi.RESET} {self._person(member.name)}{ansi.BOLD}"
            f"{member.name}{ansi.RESET}{role}",
            f"{t.dim_style}{'─' * max(0, width - 1)}{ansi.RESET}",
        ]
        meta = f" {t.hint_style}{member.model or '—'}{ansi.RESET}"
        meta += (
            f"{t.dim_style} · {member.age}m · {member.turns} turns"
            f" · {member.tools} tools"
            f" · read #{member.last_read}{ansi.RESET}"
        )
        if member.fails:
            meta += f"{t.swarm.fail} · {member.fails} failed{ansi.RESET}"
        if unread:
            meta += f"{t.swarm.accent} · +{unread} unread{ansi.RESET}"
        rows.append(ansi.clip(meta, width))
        now = self._now_line(member, max(8, width - 6))
        rows.append(ansi.clip(f" {t.swarm.accent}NOW{ansi.RESET}  {now}", width))
        rows.append("")

        events = [event for event in member.events]
        posts = self.view.posts_by(member.name)
        room = max(0, height - len(rows) - 3)
        shown_events = events[-max(1, room * 3 // 5) :] if room else []
        shown_posts = posts[-max(0, room - len(shown_events) - 1) :] if room else []
        rows.append(f" {t.swarm.accent}ACTIVITY{ansi.RESET}")
        # One member's panel: the author column would repeat the same name on
        # every row, so it goes and the detail gets the room instead.
        rows.extend(self._event_row(event, width, author=False) for event in shown_events)
        if not shown_events:
            rows.append(f" {t.dim_style}no tool calls yet{ansi.RESET}")
        rows.append("")
        rows.append(f" {t.swarm.accent}POSTS ({len(posts)}){ansi.RESET}")
        for post in shown_posts:
            seq = f"{t.dim_style}#{post.seq:<3}{ansi.RESET}"
            area = ""
            if post.channel != "main" and width >= 78:
                area = f"{t.dim_style}{ansi.pad('#' + post.channel, 8)}{ansi.RESET}"
            when = f"{t.dim_style}{post.at:>8}{ansi.RESET} " if post.at and width >= 78 else ""
            room_text = max(6, width - ansi.width(seq) - ansi.width(area) - ansi.width(when) - 2)
            rows.append(
                ansi.clip(
                    f" {when}{seq} {area}{t.statusline_style}"
                    f"{ansi.clip_ellipsis(_styled(post.text), room_text)}{ansi.RESET}",
                    width,
                )
            )
        if not posts:
            rows.append(f" {t.dim_style}nothing yet{ansi.RESET}")
        return rows[:height]

    # -- keys --------------------------------------------------------------

    def key(self, name: str) -> str:
        """Handle one key. Returns ``"close"``, ``"redraw"``, an ask answer, or
        ``"ignore"``. An answer is ``"approve"``, ``"allow"`` or ``"deny"``, and
        only means something while :attr:`ask` is set."""
        if self.ask is not None:
            return self._ask_key(name)
        ordered = self.view.ordered()
        if name in ("left", "esc", "q"):
            if self.open_member:
                self.open_member = ""
                return "redraw"
            if self.help:
                self.help = False
                return "redraw"
            return "close"
        if name == "?":
            self.help = not self.help
            return "redraw"
        if name == "tab":
            self.focus = "feed" if self.focus == "roster" else "roster"
            return "redraw"
        if name == "v":
            self.panel = "board" if self.panel == "activity" else "activity"
            return "redraw"
        if name in ("up", "down", "k", "j"):
            delta = -1 if name in ("up", "k") else 1
            if self.focus == "roster" or self.open_member:
                if ordered:
                    self.selected = max(0, min(len(ordered) - 1, self.selected + delta))
            elif delta < 0:
                self.follow = False
                self.scroll = min(max(0, len(self.shown_events()) - 1), self.scroll + 1)
            else:
                self.scroll = max(0, self.scroll - 1)
            return "redraw"
        if name in ("enter", "o"):
            if ordered:
                member = ordered[min(self.selected, len(ordered) - 1)]
                self.open_member = "" if self.open_member == member.name else member.name
                self.focus = "roster"
            return "redraw"
        if name == "f":
            self.follow = not self.follow
            if self.follow:
                self.scroll = 0
            return "redraw"
        if name in (" ", "space"):
            self.paused = not self.paused
            return "redraw"
        if name.isdigit() and name != "0":
            channels = ["all", "main", *self.view.channels]
            index = int(name)
            if 1 <= index <= len(channels):
                self.channel = channels[index - 1]
            return "redraw"
        return "ignore"

    def _ask_key(self, name: str) -> str:
        ask = self.ask
        if ask is None:
            return "ignore"
        if name in ("up", "k"):
            ask.selected = (ask.selected - 1) % 3
            return "redraw"
        if name in ("down", "j", "tab"):
            ask.selected = (ask.selected + 1) % 3
            return "redraw"
        if name in ("1", "2", "3"):
            ask.selected = int(name) - 1
            return ask.choice
        if name == "enter":
            # Space does not answer: it is the pause key next to the confirm
            # key, and an approval is the one thing on this page that must not
            # be one keystroke away from a reflex.
            return ask.choice
        if name in ("esc", "left", "n"):
            return "deny"
        return "redraw"  # nothing else is live while a worker waits on you

    def _overlay_help(self, rows: list[str], width: int, height: int) -> list[str]:
        t = theme.current()
        lines = [
            f"{t.tag_style}{ansi.BOLD}keys{ansi.RESET}",
            "",
            f"{t.hint_style}↑ ↓{ansi.RESET}   move the selection",
            f"{t.hint_style}⏎{ansi.RESET}     open the member's panel",
            f"{t.hint_style}esc{ansi.RESET}   back, then close the board",
            f"{t.hint_style}tab{ansi.RESET}   switch between roster and activity",
            f"{t.hint_style}v{ansi.RESET}     activity timeline or board posts only",
            f"{t.hint_style}1-9{ansi.RESET}   pick a channel",
            f"{t.hint_style}f{ansi.RESET}     follow the tail",
            f"{t.hint_style}space{ansi.RESET} pause the board",
            f"{t.hint_style}left{ansi.RESET}  close",
        ]
        box_w = min(width - 6, max(34, *(ansi.width(line) + 6 for line in lines)))
        box_h = len(lines) + 2
        top = max(0, (height - box_h) // 2)
        left = max(0, (width - box_w) // 2)
        out = list(rows)
        for index in range(box_h):
            if not 0 <= top + index < len(out):
                continue
            if index == 0:
                box = f"{t.divider_style}╭{'─' * (box_w - 2)}╮{ansi.RESET}"
            elif index == box_h - 1:
                box = f"{t.divider_style}╰{'─' * (box_w - 2)}╯{ansi.RESET}"
            else:
                box = (
                    f"{t.divider_style}│{ansi.RESET} "
                    + ansi.pad(lines[index - 1], box_w - 4)
                    + f" {t.divider_style}│{ansi.RESET}"
                )
            base = ansi.clip(out[top + index], left)
            out[top + index] = ansi.pad(base, left) + box + " " * max(0, width - left - box_w)
        return out

    @staticmethod
    def _fit(rows: list[str], height: int) -> list[str]:
        out = list(rows[:height])
        while len(out) < height:
            out.append("")
        return out


#: A post's doorbell: ``@name``, ``@all`` and ``@#channel``, styled together.
MENTION_RE = re.compile(r"(@#?[A-Za-z0-9][A-Za-z0-9_-]*|@all)")


def _styled(text: str) -> str:
    """Mentions in the accent: they are the doorbell, so they read as one."""
    t = theme.current()
    out: list[str] = []
    for piece in MENTION_RE.split(text):
        if piece.startswith("@"):
            out.append(f"{ansi.RESET}{t.swarm.accent}{piece}{ansi.RESET}")
        else:
            out.append(piece)
    return "".join(out)


__all__ = [
    "MIN_COLS",
    "MIN_ROWS",
    "STATES",
    "Ask",
    "EventView",
    "MemberView",
    "PostView",
    "SwarmBoard",
    "SwarmView",
    "duration",
    "snapshot_from_engine",
    "sparkline",
    "tool_glyph",
]
