"""The swarm board: the page ``left`` opens when there is a swarm to watch.

A swarm is a room, not a list of agents. This draws the room: one roster column
where every member is a card that says what it is doing right now, one feed
column where the shared board scrolls past, and a header that counts the states
and names the head. Open a member and its column becomes that member's own panel
— its activity, its posts, where it works — so "what is this one doing" never
means scrolling the board.

The page reads a :class:`SwarmView`, which :func:`snapshot_from_engine` builds
from the live :class:`vx.swarm.SwarmEngine`. Nothing here imports the agent
harness: this module is layout and keys, and stays testable without a model.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from vx.tui import ansi, theme

#: Below this a board is a diagram of a board: it says what it needs instead.
MIN_COLS = 52
MIN_ROWS = 10

#: The roster column: wide enough for a name and its numbers, never a third of
#: the room. The feed is where the words are.
ROSTER_MIN = 26
ROSTER_MAX = 42

#: The states, in the order the roster lists them, with the glyph and the word.
STATES: tuple[tuple[str, str, str], ...] = (
    ("blocked", "◆", "needs you"),
    ("working", "◈", "working"),
    ("waiting", "⏸", "waiting"),
    ("stopped", "○", "idle"),
    ("killed", "✕", "killed"),
)

STATE_ORDER = {name: index for index, (name, _glyph, _word) in enumerate(STATES)}


# ---------------------------------------------------------------------------
# what the page draws: a snapshot of the swarm, taken by the app each frame
# ---------------------------------------------------------------------------


@dataclass
class MemberView:
    """One agent in the room, as the board draws it."""

    name: str
    role: str = "worker"
    model: str = ""
    state: str = "working"  # working | waiting | stopped | killed
    channels: tuple[str, ...] = ()
    workspace: str = "inherit"
    turns: int = 0
    age: int = 0
    last_read: int = 0
    added_by: str = "coordinator"
    activity: str = ""
    note: str = ""
    pending_model: str = ""

    @property
    def channel(self) -> str:
        return self.channels[0] if self.channels else "main"


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


def _activity_of(member: Any) -> str:
    """What a live member is doing, from the little the engine knows for certain.

    Only two things are true of a worker without asking its model: the state the
    engine reports, and the last thing it said before it parked. A page that
    invented more than that would be lying about a live board.
    """
    if getattr(member.row, "killed", False) or member.state == "killed":
        return "killed — its work needs a fresh worker"
    awaiting = getattr(member, "awaiting", "")
    if awaiting:
        return f"needs you · {awaiting}"
    if member.state == "waiting":
        return f"parked in swarm_wait · since #{member.row.last_read_seq}"
    if member.state == "stopped":
        return "between turns · a post naming it wakes it"
    if getattr(member, "pending_model", ""):
        return f"switching to {member.pending_model} at its next turn"
    text = " ".join((getattr(member, "last_text", "") or "").split())
    if text:
        return text[:160]
    return "working"


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
                activity=_activity_of(live),
                note=row.note,
                pending_model=getattr(live, "pending_model", ""),
            )
        )
    posts = [
        PostView(post.seq, post.agent, post.text, post.channel, post.time[-5:])
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
    )


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------


@dataclass
class SwarmBoard:
    """Header, channels, roster, feed, and any member's own panel.

    Every band is budgeted before it is drawn, so a short terminal loses feed rows
    before it loses the roster, and loses both before it loses the header. A frame
    is exactly ``height`` rows of exactly ``width`` cells at any size.
    """

    view: SwarmView = field(default_factory=SwarmView)
    selected: int = 0
    focus: str = "roster"  # roster | feed
    channel: str = "all"
    follow: bool = True
    scroll: int = 0
    open_member: str = ""
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
                f"{theme.current().hint_style}⬡ vx swarm{ansi.RESET}",
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
                f"{t.dim_style}⬡{ansi.RESET} {t.tag_style}{ansi.BOLD}vx swarm{ansi.RESET}",
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
        left = f"{t.dim_style}⬡{ansi.RESET} {t.tag_style}{ansi.BOLD}{self.view.title}{ansi.RESET}"
        chips: list[str] = []
        for name, glyph, word in STATES:
            count = counts.get(name, 0)
            if not count:
                continue
            style = {
                "blocked": t.warning_style,
                "working": t.tag_style,
                "waiting": t.dim_style,
                "stopped": t.dim_style,
                "killed": t.red_style,
            }[name]
            chips.append(f"{style}{glyph}{ansi.RESET}{t.dim_style} {count} {word}{ansi.RESET}")
        head = f"{t.dim_style}board #{self.view.head}{ansi.RESET}"
        right = f"{'   '.join(chips)}   {head}"
        if ansi.width(left) + ansi.width(right) + 2 > width:
            right = head
        if ansi.width(left) + ansi.width(right) + 2 > width:
            right = ""
        return ansi.clip(ansi.pad(left, max(1, width - ansi.width(right))) + right, width)

    def _rule(self, width: int) -> str:
        t = theme.current()
        return f"{t.divider_style}{'─' * max(0, width)}{ansi.RESET}"

    def _strip(self, width: int) -> str:
        t = theme.current()
        parts = [self._channel_chip("all"), self._channel_chip("main")]
        parts.extend(self._channel_chip(name) for name in self.view.channels)
        left = " " + f"{t.dim_style}   {ansi.RESET}".join(parts)
        fresh = 0 if self.follow else max(0, self.view.head - self.seen_head)
        state = (
            f"{t.dim_style}paused{ansi.RESET}"
            if self.paused
            else (
                f"{t.tag_style}● following{ansi.RESET}"
                if self.follow
                else f"{t.dim_style}‖ held{ansi.RESET}"
            )
        )
        if fresh:
            state += f"{t.warning_style}  ▲ {fresh} new{ansi.RESET}"
        row = left
        if ansi.width(row) + ansi.width(state) + 1 <= width:
            row = ansi.pad(row, width - ansi.width(state)) + state
        return ansi.clip(row, width)

    def _channel_chip(self, name: str) -> str:
        t = theme.current()
        label = f"#{name}"
        count = 0
        if name not in ("all", "main"):
            count = sum(1 for m in self.view.members if name in m.channels)
        text = f"{label}{f' {count}' if count else ''}"
        if self.channel == name:
            return f"{t.tag_style}{ansi.BOLD}{text}{ansi.RESET}"
        return f"{t.dim_style}{text}{ansi.RESET}"

    def _footer(self, width: int) -> str:
        t = theme.current()
        if self.ask is not None:
            keys = "1-3 answer · ↑↓ choose · enter confirm · esc deny"
        elif self.open_member:
            keys = "esc back · ↑↓ member · tab roster · left close"
        elif self.focus == "feed":
            keys = "tab roster · ↑↓ scroll · f follow · ⏎ member · ? keys · left close"
        else:
            keys = "↑↓ select · ⏎ open member · tab feed · 1-9 channel · ? keys · left close"
        hint = f"{t.dim_style}{ansi.clip_ellipsis(keys, width)}{ansi.RESET}"
        if self.open_member:
            tag = f"{t.dim_style}viewing @{self.open_member}{ansi.RESET}"
            if ansi.width(tag) + 2 < width:
                return ansi.pad(tag, width - ansi.width(hint)) + hint
        return ansi.pad(hint, width)

    # -- the body ----------------------------------------------------------

    def _body(self, width: int, height: int) -> list[str]:
        if height <= 0:
            return []
        if width >= 84:
            roster_w = max(ROSTER_MIN, min(ROSTER_MAX, width * 38 // 100))
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
        return self._feed(width, height)

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
        rows = [
            self._rule(width),
            ansi.clip(
                f" {t.warning_style}◆{ansi.RESET} {t.tag_style}{ansi.BOLD}@{name}{ansi.RESET}"
                f"{t.hint_style} wants to run {ansi.BOLD}{ask.label}{ansi.DIM_OFF}{ansi.RESET}",
                width,
            ),
            ansi.clip(f" {t.dim_style}$ {ask.target_line(width - 4)}{ansi.RESET}", width),
        ]
        chips: list[str] = []
        for index, option in enumerate(ask.options):
            label = f"{index + 1} {option}"
            if index == ask.selected:
                chips.append(f"{t.tag_style}{ansi.BOLD}▸ {label}{ansi.RESET}")
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
        t = theme.current()
        count = sum(1 for member in members if member.state == state)
        word = dict((name, word) for name, _glyph, word in STATES).get(state, state)
        return f"{t.dim_style}{word.upper()} {count}{ansi.RESET}"

    def _card(self, member: MemberView, width: int, chosen: bool, *, compact: bool) -> list[str]:
        t = theme.current()
        indent = "  " * self._depth(member)
        glyph, style = self._state_face(member)
        bar = f"{t.tag_style}▌{ansi.RESET} " if chosen else "  "
        name_style = f"{t.tag_style}{ansi.BOLD}" if chosen else t.hint_style
        name = f"{name_style}{member.name}{ansi.RESET}"
        if member.role not in ("worker", "") and member.role != member.name:
            name += f"{t.dim_style} · {member.role}{ansi.RESET}"
        numbers = self._numbers(member)
        room = width - ansi.width(bar) - len(indent) - 2 - ansi.width(numbers) - 1
        head = f"{bar}{indent}{style}{glyph}{ansi.RESET} "
        head += ansi.clip_ellipsis(name, max(4, room))
        first = ansi.pad(head, max(1, width - ansi.width(numbers))) + numbers
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
        lead = f"{bar}{indent}   "
        room = max(6, width - ansi.width(lead))
        where = ansi.clip_ellipsis(self._where_line(member), room)
        activity = ansi.clip_ellipsis(member.activity, room)
        return [
            ansi.clip(first, width),
            f"{lead}{t.dim_style}{where}{ansi.RESET}",
            f"{lead}{t.statusline_style}{activity}{ansi.RESET}",
        ]

    def _numbers(self, member: MemberView) -> str:
        t = theme.current()
        unread = "" if member.state == "killed" else self.view.unread(member)
        tail = f"{t.tag_style}+{unread}{ansi.RESET}" if unread else f"{t.dim_style}·{ansi.RESET}"
        return f"{t.dim_style}{member.age}m  {member.turns}t{ansi.RESET}  {tail}"

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
        t = theme.current()
        if member.state == "working":
            pulse = ("◇", "◈", "◆", "◈")[int(self.now * 3) % 4]
            return pulse, t.tag_style
        return {
            "blocked": ("◆", t.warning_style),
            "waiting": ("⏸", t.dim_style),
            "stopped": ("○", t.dim_style),
            "killed": ("✕", t.red_style),
        }.get(member.state, ("·", t.dim_style))

    # -- feed --------------------------------------------------------------

    def posts_for_channel(self) -> list[PostView]:
        if self.channel == "all":
            return list(self.view.posts)
        return [post for post in self.view.posts if post.channel == self.channel]

    def _feed(self, width: int, height: int) -> list[str]:
        t = theme.current()
        if height <= 0:
            return []
        posts = self.posts_for_channel()
        rows: list[str] = []
        if height >= 3:
            rows.append(self._feed_header(width))
            height -= 1
        if not posts:
            rows.append(f"{t.dim_style}  the board is empty{ansi.RESET}")
            return rows
        room = max(0, height)
        tail = posts if self.follow else posts[: max(0, len(posts) - self.scroll)]
        shown = tail[-room:] if room else []
        if rows and len(shown) < len(tail):
            rows[-1] = self._feed_header(width, hidden=len(tail) - len(shown))
        body = [
            self._post_row(post, width, newest=index == len(shown) - 1)
            for index, post in enumerate(shown)
        ]
        pad = max(0, height - len(body))
        return rows + [""] * pad + body

    def _feed_header(self, width: int, hidden: int = 0) -> str:
        t = theme.current()
        where = "every channel" if self.channel == "all" else f"#{self.channel}"
        label = f"{t.hint_style}{ansi.BOLD}BOARD{ansi.DIM_OFF}"
        left = f"{t.dim_style}▾{ansi.RESET} {label} {t.dim_style}· {where}{ansi.RESET}"
        count = len(self.posts_for_channel())
        right = f"{t.dim_style}{count} post{'s' if count != 1 else ''}{ansi.RESET}"
        if self.channel not in ("all",) and self.channel in self.view.channels:
            topic = self.view.channels[self.channel]
            right = f"{t.dim_style}{ansi.clip_ellipsis(topic, max(0, width // 2))}{ansi.RESET}"
        if hidden:
            right = f"{t.dim_style}+{hidden} older{ansi.RESET}"
        if ansi.width(left) + ansi.width(right) + 1 <= width:
            return ansi.pad(left, width - ansi.width(right)) + right
        return ansi.clip(left, width)

    def _post_row(self, post: PostView, width: int, *, newest: bool = False) -> str:
        t = theme.current()
        seq = f"{t.dim_style}#{post.seq:<3}{ansi.RESET}"
        where = ""
        if width >= 70:
            label = "" if post.channel == "main" else f"#{post.channel}"
            where = f"{t.dim_style}{ansi.pad(label, 8)}{ansi.RESET}"
        author = f"{t.tag_style}@{ansi.clip_ellipsis(post.agent, 11)}{ansi.RESET}"
        lead = f" {seq} {where}{ansi.pad(author, 14)}"
        room = max(6, width - ansi.width(lead) - 1)
        body_style = t.hint_style if newest else t.statusline_style
        body = ansi.clip_ellipsis(_styled(post.text), room)
        return ansi.clip(f"{lead}{body_style}{body}{ansi.RESET}", width)

    # -- one member's panel ------------------------------------------------

    def _member_panel(self, name: str, width: int, height: int) -> list[str]:
        member = self.view.member(name)
        t = theme.current()
        if member is None:
            return [f"{t.dim_style}  no member {name!r}{ansi.RESET}"]
        glyph, style = self._state_face(member)
        unread = self.view.unread(member)
        head = (
            f"{t.dim_style}▾{ansi.RESET} {t.hint_style}{ansi.BOLD}MEMBER{ansi.DIM_OFF}{ansi.RESET}"
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
            f" {style}{glyph}{ansi.RESET} {t.tag_style}{ansi.BOLD}{member.name}{ansi.RESET}{role}",
            f"{t.dim_style}{'─' * max(0, width - 1)}{ansi.RESET}",
        ]
        meta = f" {t.hint_style}{member.model or '—'}{ansi.RESET}"
        meta += (
            f"{t.dim_style} · {member.age}m · {member.turns} turns"
            f" · read #{member.last_read}{ansi.RESET}"
        )
        if unread:
            meta += f"{t.tag_style} · +{unread} unread{ansi.RESET}"
        rows.append(ansi.clip(meta, width))
        rows.append("")
        rows.append(f" {t.dim_style}ACTIVITY{ansi.RESET}")
        rows.append(
            f" {t.hint_style}{ansi.clip_ellipsis(member.activity, max(6, width - 3))}{ansi.RESET}"
        )
        rows.append("")
        mine = self.view.posts_by(member.name)
        rows.append(f" {t.dim_style}POSTS ({len(mine)}){ansi.RESET}")
        body_h = max(0, height - len(rows))
        for post in mine[-body_h:] if body_h else []:
            seq = f"{t.dim_style}#{post.seq:<3}{ansi.RESET}"
            area = ""
            if post.channel != "main" and width >= 78:
                area = f"{t.dim_style}{ansi.pad('#' + post.channel, 8)}{ansi.RESET}"
            room = max(6, width - ansi.width(seq) - ansi.width(area) - 2)
            rows.append(
                ansi.clip(
                    f" {seq} {area}{t.statusline_style}"
                    f"{ansi.clip_ellipsis(_styled(post.text), room)}{ansi.RESET}",
                    width,
                )
            )
        if not mine:
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
        if name in ("up", "down", "k", "j"):
            delta = -1 if name in ("up", "k") else 1
            if self.focus == "roster" or self.open_member:
                if ordered:
                    self.selected = max(0, min(len(ordered) - 1, self.selected + delta))
            elif delta < 0:
                self.follow = False
                self.scroll = min(max(0, len(self.posts_for_channel()) - 1), self.scroll + 1)
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
        if name in ("enter", " "):
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
            f"{t.hint_style}tab{ansi.RESET}   switch between roster and feed",
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
            out.append(f"{ansi.RESET}{t.tag_style}{piece}{ansi.RESET}")
        else:
            out.append(piece)
    return "".join(out)


__all__ = [
    "MIN_COLS",
    "MIN_ROWS",
    "STATES",
    "MemberView",
    "PostView",
    "SwarmBoard",
    "SwarmView",
    "snapshot_from_engine",
]
