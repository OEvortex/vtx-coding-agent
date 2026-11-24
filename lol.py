#!/usr/bin/env python3
"""lol.py — a live vx session with no model behind it.

The screen, the keys and the painter are vx's own: the same blocks, viewport,
footer rows and incremental painter the shell uses, fed from fixtures instead of
an agent. It stays on the alternate screen and keeps taking keys until you leave
(ctrl+c, or ctrl+d on an empty composer), so a layout, colour or scroll question
can be poked at instead of squinted at.

    python lol.py                    # interactive session
    python lol.py -w 60 -H 20        # ... at a fixed size
    python lol.py --snapshot         # print one frame and exit (for piping)
    python lol.py --only diff        # one fixture, no screen
    python lol.py --list             # fixture names

Keys: type and press enter (the turn is simulated), / for commands, pgup/pgdn and
wheel-less arrows scroll, ctrl+o expands, ctrl+l repaints, esc interrupts.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from vx.tui import ansi, blocks, footer, theme
from vx.tui.canvas import Painter
from vx.tui.keys import KeyDecoder
from vx.tui.terminal import Terminal
from vx.tui.viewport import Viewport

#: Same floor the shell uses when it composes a frame.
MIN_HEIGHT = 8
#: Longest input the composer keeps, as the shell caps it.
MAX_INPUT_LINES = 8


# ---------------------------------------------------------------------------
# fixtures: one builder per element kind
# ---------------------------------------------------------------------------


def welcome() -> blocks.Block:
    return blocks.Welcome(version="0.1.0")


def user_turn(text: str | None = None, attachments: str = "") -> blocks.Block:
    return blocks.UserTurn(
        text=text
        or "Refactor the transcript viewport so scrolling never jumps, then run "
        "the viewport tests and tell me what changed.",
        attachments=attachments,
    )


def assistant() -> blocks.Block:
    return blocks.Assistant(
        text="""The jump came from two places, and both are fixed.

## What changed

- `Viewport.add` no longer resets `scroll` to 0, so a streaming tool call cannot
  drag the reader back to the tail.
- `_rebuild` moves the anchor by the rows it appended, keeping the visible text
  where it was.

```python
def add(self, block: blocks.Block) -> None:
    self.blocks.append(block)
    self._dirty = True
```

Everything else — spacing, the pinned banner, the fold marker — is untouched.
`ruff check` and the 286 viewport tests are green."""
    )


THOUGHT = (
    "The user reports the view jumping while a turn streams. Look at how add() "
    "treats the scroll offset, then at what the rebuild does with the row count. "
    "The anchor has to survive a growing tail."
)


def thinking_done() -> blocks.Block:
    return blocks.Thinking(text=THOUGHT, streaming=False, elapsed=4.0)


def thinking_live() -> blocks.Block:
    return blocks.Thinking(
        text="Reading the viewport now to see where the offset is reset, and "
        "whether the pinned banner interferes with the window arithmetic.",
        streaming=True,
    )


def tools_read_group() -> blocks.Block:
    return blocks.ToolGroup(
        calls=[
            blocks.ReadToolStatus(
                path="packages/vx/src/vx/tui/viewport.py",
                start_line=1,
                end_line=60,
                total_lines=185,
                status="ok",
                elapsed=0.2,
            ),
            blocks.GrepToolStatus(
                pattern="self.scroll",
                path="packages/vx/src/vx",
                match_count=7,
                status="ok",
                elapsed=0.4,
            ),
            blocks.BashToolStatus(
                command="uv run python -m pytest packages/vx/tests/test_viewport.py -q",
                exit_code=0,
                status="ok",
                elapsed=1.4,
            ),
        ]
    )


def tools_mixed_group() -> blocks.Block:
    return blocks.ToolGroup(
        calls=[
            blocks.BashToolStatus(
                command="uv run python -m pytest packages/vx/tests/test_nope.py -q",
                exit_code=2,
                status="error",
                reason="file or directory not found",
                elapsed=0.3,
            ),
            blocks.ApplyPatchToolStatus(
                path="packages/vx/src/vx/tui/viewport.py",
                added=3,
                removed=1,
                status="ok",
                elapsed=0.1,
            ),
            blocks.WebToolStatus(
                name="web",
                query="textual viewport anchor scroll offset",
                mode="search",
                result_count=5,
                status="ok",
                elapsed=2.2,
            ),
        ]
    )


def tools_single() -> blocks.Block:
    return blocks.ToolGroup(
        calls=[
            blocks.BashToolStatus(
                command="git status --porcelain", exit_code=0, status="ok", elapsed=0.05
            )
        ]
    )


def tools_running() -> blocks.Block:
    return blocks.ToolGroup(
        calls=[
            blocks.ReadToolStatus(
                path="packages/vx/src/vx/app.py",
                start_line=2100,
                end_line=2130,
                total_lines=2400,
                status="running",
            )
        ]
    )


DIFF_LINES: list[tuple[str, str, str]] = [
    (" ", "83", "    def _rebuild(self, width: int) -> None:"),
    (" ", "84", "        rows, heights = self._build(width)"),
    ("-", "85", "        self._rows = rows"),
    ("+", "85", "        if self.scroll > 0:"),
    ("+", "86", "            # Hold the reader's place when rows land below the window."),
    ("+", "87", "            self.scroll = max(0, self.scroll + len(rows) - len(self._rows))"),
    ("+", "88", "        self._rows = rows"),
    (" ", "89", "        self._width = width"),
    (" ", "90", "        self._dirty = False"),
]


def apply_patch(long_line: bool = False) -> list[blocks.Block]:
    lines = list(DIFF_LINES)
    added = 3
    if long_line:
        # A line past the split's half-width, to watch it wrap instead of clip.
        lines.append(
            (
                "+",
                "91",
                "        anchor = max(0, min(self.scroll + len(rows) - len(self._rows), "
                "self._max_scroll(width, height)))",
            )
        )
        added = 4
    return [
        blocks.ToolGroup(
            calls=[
                blocks.ApplyPatchToolStatus(
                    path="packages/vx/src/vx/tui/viewport.py",
                    added=added,
                    removed=1,
                    status="ok",
                    elapsed=0.1,
                )
            ]
        ),
        blocks.DiffOutput(lines=lines, path="packages/vx/src/vx/tui/viewport.py"),
    ]


def shell_run() -> blocks.Block:
    return blocks.ShellRun(
        command="rg -n 'self.scroll' packages/vx/src/vx | head",
        output="viewport.py:63:        self.viewport.scroll = 0\n"
        "viewport.py:145:        self.scroll = max(0, min(self.scroll, "
        "self._max_scroll(width, height)))\n"
        "viewport.py:178:    def scroll_by(self, delta: int, width: int, height: int) -> None:\n"
        "app.py:559:                self.viewport.page(1, *self._size())\n"
        "app.py:567:        self.viewport.scroll = 0\n"
        "app.py:1301:        self.viewport.scroll = 0",
        done=True,
        success=True,
    )


def shell_run_failed() -> blocks.Block:
    return blocks.ShellRun(
        command="make release",
        output="make: *** No rule to make target 'release'.  Stop.",
        done=True,
        success=False,
    )


def notices() -> list[blocks.Block]:
    return [
        blocks.Notice(
            topic="model", body="switched to deepai/deepseek-v4.1-flash", tone="success"
        ),
        blocks.Notice(
            topic="history",
            body="scrollback limit reached",
            detail="dropped 1,240 earlier rows · raise it in /settings",
            tone="warning",
        ),
        blocks.Notice(
            topic="provider", body="invalid API key · check ~/.ctx/config.yml", tone="error"
        ),
        blocks.Notice(
            topic="", body="plain notice with no topic, body on the same row", tone="information"
        ),
    ]


def summary() -> blocks.Block:
    return blocks.TurnSummary(
        recap="Ran 3 commands · edited 1 file", seconds=7.0, tokens_in=30, tokens_out=88
    )


def history_fold() -> blocks.Block:
    return blocks.HistoryFold(dropped=1240)


def sections() -> blocks.Block:
    return blocks.Sections(
        title="Session",
        sections=[
            (
                "Runtime",
                [
                    ("model", "deepai/deepseek-v4.1-flash"),
                    ("context", "56k / 320k · 17%"),
                    ("thinking", "2 rows folded"),
                ],
            ),
            ("Tools", [("read", "enabled"), ("bash", "enabled · ask")]),
        ],
        detail="use /settings to change any of these",
    )


def usage() -> blocks.Block | None:
    """The /usage block needs ctx's report types; skip when they are absent."""
    try:
        from ctx.term.usage_stats import DailyUsage, UsageReport
    except Exception:
        return None
    today = date.today()
    days: dict[date, DailyUsage] = {}
    for offset in range(120):
        day = today - timedelta(days=offset)
        weight = (offset * 7) % 11
        days[day] = DailyUsage(
            input_tokens=weight * 91_000,
            output_tokens=weight * 8_100,
            cache_read_tokens=weight * 3_400,
            turns=weight,
        )
    report = UsageReport(
        days=days,
        lifetime=DailyUsage(
            input_tokens=16_800_000_000,
            output_tokens=59_000_000,
            cache_read_tokens=12_200_000_000,
            turns=148_157,
        ),
        last_30_days=DailyUsage(
            input_tokens=1_500_000_000,
            output_tokens=13_400_000,
            cache_read_tokens=2_400_000_000,
            turns=41_262,
        ),
        sessions_scanned=136,
        as_of=today,
    )
    session = DailyUsage(
        input_tokens=56_000, output_tokens=1_800, cache_read_tokens=12_000, turns=3
    )
    return blocks.Usage(session=session, report=report)


#: Fixture name -> builder. A builder returns one block or a list of them.
FIXTURES: dict[str, Any] = {
    "welcome": welcome,
    "user": user_turn,
    "thinking": thinking_done,
    "assistant": assistant,
    "tools": tools_read_group,
    "tools-mixed": tools_mixed_group,
    "tools-single": tools_single,
    "tools-running": tools_running,
    "patch": apply_patch,
    "patch-long": lambda: apply_patch(long_line=True),
    "shell": shell_run,
    "shell-failed": shell_run_failed,
    "notices": notices,
    "summary": summary,
    "fold": history_fold,
    "sections": sections,
    "usage": usage,
}


def build(name: str) -> list[blocks.Block]:
    builder = FIXTURES.get(name)
    if builder is None:
        raise SystemExit(f"unknown fixture: {name}\nknown: {', '.join(FIXTURES)}")
    built = builder()
    if built is None:
        return []
    return [built] if isinstance(built, blocks.Block) else list(built)


#: The opening session: enough to fill a window and scroll in.
OPENING: tuple[str, ...] = (
    "welcome",
    "user",
    "thinking",
    "assistant",
    "tools",
    "patch-long",
    "summary",
)


def opening() -> list[blocks.Block]:
    out: list[blocks.Block] = []
    for name in OPENING:
        out.extend(build(name))
    return out


# ---------------------------------------------------------------------------
# the pieces the opening session and the commands both draw
# ---------------------------------------------------------------------------


@dataclass
class Command:
    """One ``/`` entry, and what the harness does when it is picked."""

    label: str
    detail: str
    metadata: str
    action: str


COMMANDS: list[Command] = [
    Command("/run", "simulate a turn: a streaming thought, then a tool call", "Demo", "run"),
    Command("/diff", "append an apply_patch diff, split or one column", "Demo", "diff"),
    Command("/tools", "append a mixed tool group: failure, edit, search", "Demo", "tools"),
    Command("/usage", "append the /usage report over a year", "Demo", "usage"),
    Command("/notices", "append one notice of every tone", "Demo", "notices"),
    Command("/sections", "append a titled report", "Demo", "sections"),
    Command("/shell", "append a !command echo", "Demo", "shell"),
    Command("/theme", "switch between fx dark and fx light", "View", "theme"),
    Command("/narrow", "resize the layout to 60 columns", "View", "narrow"),
    Command("/wide", "resize the layout to 120 columns", "View", "wide"),
    Command("/clear", "drop the transcript back to the banner", "Session", "clear"),
    Command("/keys", "print the keymap into the transcript", "Session", "keys"),
    Command("/quit", "leave the harness", "Session", "quit"),
]


def menu_items(matches: list[Command]) -> list[footer.MenuItem]:
    return [
        footer.MenuItem(label=cmd.label, detail=cmd.detail, metadata=cmd.metadata)
        for cmd in matches
    ]


def keymap() -> blocks.Block:
    return blocks.Sections(
        title="Keys",
        sections=[
            (
                "Scroll",
                [
                    ("pgup / pgdn", "a screen at a time"),
                    ("up / down", "a row at a time"),
                    ("home / end", "the top, or back to the tail"),
                ],
            ),
            (
                "Read",
                [("ctrl+o", "expand tool output and thoughts"), ("ctrl+l", "repaint every row")],
            ),
            ("Leave", [("ctrl+c", "quit now"), ("ctrl+d", "quit when the composer is empty")]),
        ],
        detail="the transcript is a record: these only change what is on screen",
    )


# ---------------------------------------------------------------------------
# the live session
# ---------------------------------------------------------------------------


@dataclass
class LiveTurn:
    """A turn the harness plays back, so something is always moving."""

    started: float
    thought: blocks.Thinking
    call: blocks.BashToolStatus
    group: blocks.ToolGroup
    words: list[str]
    said: int = 0
    stage: int = 0

    def tick(self, now: float) -> bool:
        """Advance the turn; True while it is still running."""
        elapsed = now - self.started
        if self.stage == 0:
            due = int(elapsed * 12)
            while self.said < min(due, len(self.words)):
                self.thought.text = (
                    f"{self.thought.text} {self.words[self.said]}".strip()
                    if self.thought.text
                    else self.words[self.said]
                )
                self.said += 1
            if self.said >= len(self.words):
                self.stage = 1
                self.thought.streaming = False
                self.thought.elapsed = elapsed
            return True
        if self.stage == 1 and elapsed >= 2.4:
            self.call.status = "ok"
            self.call.exit_code = 0
            self.call.elapsed = elapsed - 1.2
            self.stage = 2
        return False


class Harness:
    def __init__(
        self, term: Terminal, *, width: int | None = None, history: int | None = None
    ) -> None:
        self.term = term
        self.painter = Painter()
        self.viewport = Viewport()
        self.decoder = KeyDecoder()
        self.text = ""
        self.cursor = 0
        self.menu_open = False
        self.menu_selected = 0
        self.live: LiveTurn | None = None
        self.queued = 0
        self.started = time.monotonic()
        self._fixed_width = width
        self._resized = False
        self._running = True
        self.permission = "auto"
        self.model = "example-model"
        self.effort = "default"
        self.context_used = 56_000
        self.context_total = 320_000
        self.workspace = os.getcwd()
        self.branch = "main"
        if history:
            # A capped transcript grows a fold marker instead of a silent cut.
            self.viewport.set_history_limit(history)
        for block in opening():
            self.viewport.add(block)

    # -- sizing ------------------------------------------------------------

    def size(self) -> tuple[int, int]:
        rows, cols = self.term.size()
        if self._fixed_width:
            cols = self._fixed_width
        return max(MIN_HEIGHT, rows), max(20, cols)

    def resized(self) -> None:
        self._resized = True

    # -- transcript --------------------------------------------------------

    def add(self, *built: blocks.Block) -> None:
        for block in built:
            self.viewport.add(block)
        self.viewport.touch()

    # -- the live turn -----------------------------------------------------

    def start_turn(self) -> None:
        thought = blocks.Thinking(text="", streaming=True)
        call = blocks.BashToolStatus(
            command="uv run python -m pytest packages/vx/tests -q", status="running"
        )
        group = blocks.ToolGroup(calls=[call])
        self.live = LiveTurn(
            started=time.monotonic(),
            thought=thought,
            call=call,
            group=group,
            words=[
                "Checking",
                "what",
                "the",
                "viewport",
                "reports",
                "when",
                "rows",
                "land",
                "below",
                "the",
                "window,",
                "then",
                "whether",
                "the",
                "anchor",
                "moves",
                "with",
                "them",
                "or",
                "resets.",
            ],
        )
        self.add(thought, group)

    def cancel_turn(self) -> None:
        if self.live is None:
            return
        self.live.call.status = "cancelled"
        self.live.thought.streaming = False
        self.live.thought.elapsed = time.monotonic() - self.live.started
        self.viewport.touch()
        self.live = None
        self.add(blocks.Notice(topic="turn", body="interrupted", tone="cancelled"))

    def tick(self) -> bool:
        """Advance whatever is animating; True when a row changed."""
        if self.live is None:
            return False
        running = self.live.tick(time.monotonic())
        self.viewport.touch()
        if not running:
            self.add(summary())
            self.live = None
        return True

    # -- keys --------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        for key in self.decoder.feed(data):
            self.key(key)

    def key(self, key: str) -> None:
        if key in ("ctrl+c",):
            self._running = False
            return
        if key.startswith("paste:"):
            self.insert(key[len("paste:") :].replace("\n", " "))
            return
        if key == "esc":
            if self.menu_open:
                self.menu_open = False
                self.text = ""
                self.cursor = 0
            elif self.live is not None:
                self.cancel_turn()
            return
        if self.menu_open and self.menu_key(key):
            return
        if key == "ctrl+o":
            blocks.set_expanded(not blocks.expanded())
            self.viewport.touch()
            return
        if key == "ctrl+l":
            self.painter.reset()
            return
        if key in ("pageup", "pagedown", "up", "down", "home", "end", "ctrl+u", "ctrl+f"):
            self.scroll_key(key)
            return
        if key == "enter":
            self.submit()
            return
        if key == "tab":
            return
        if key == "backspace":
            if self.cursor:
                self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
                self.cursor -= 1
                self.sync_menu()
            return
        if key == "left":
            self.cursor = max(0, self.cursor - 1)
            return
        if key == "right":
            self.cursor = min(len(self.text), self.cursor + 1)
            return
        if key == "delete":
            self.text = self.text[: self.cursor] + self.text[self.cursor + 1 :]
            return
        if key in ("ctrl+a",):
            self.cursor = 0
            return
        if key in ("ctrl+e",):
            self.cursor = len(self.text)
            return
        if key in ("ctrl+w",):
            head = self.text[: self.cursor].rstrip()
            cut = head.rfind(" ") + 1
            self.text = self.text[:cut] + self.text[self.cursor :]
            self.cursor = cut
            self.sync_menu()
            return
        if key == "ctrl+d":
            if not self.text:
                self._running = False
            return
        if len(key) == 1 and key >= " ":
            self.insert(key)

    def insert(self, text: str) -> None:
        self.text = self.text[: self.cursor] + text + self.text[self.cursor :]
        self.cursor += len(text)
        self.sync_menu()

    def scroll_key(self, key: str) -> None:
        """Scrolling is offset-based: a bigger offset is further back in time.

        ``up``/``pageup`` therefore *raise* the offset, the same direction the
        wheel raises it in the shell.
        """
        rows, cols = self.size()
        area = max(1, rows - 3)
        if key in ("pageup", "pagedown", "ctrl+u", "ctrl+f"):
            older = key in ("pageup", "ctrl+u")
            self.viewport.page(1 if older else -1, cols, area)
            return
        if key == "up":
            self.viewport.scroll_by(1, cols, area)
        elif key == "down":
            self.viewport.scroll_by(-1, cols, area)
        elif key == "home":
            self.viewport.scroll_by(self.viewport.total(cols), cols, area)
        elif key == "end":
            self.viewport.bottom()

    # -- the / menu --------------------------------------------------------

    def matches(self) -> list[Command]:
        if not self.text.startswith("/"):
            return []
        typed = self.text.lower()
        return [cmd for cmd in COMMANDS if cmd.label.startswith(typed)] or COMMANDS

    def sync_menu(self) -> None:
        self.menu_open = self.text.startswith("/")
        if self.menu_open:
            self.menu_selected = min(self.menu_selected, max(0, len(self.matches()) - 1))

    def menu_key(self, key: str) -> bool:
        matches = self.matches()
        if key == "up":
            self.menu_selected = max(0, self.menu_selected - 1)
            return True
        if key == "down":
            self.menu_selected = min(len(matches) - 1, self.menu_selected + 1)
            return True
        if key == "tab" and matches:
            self.text = matches[self.menu_selected].label
            self.cursor = len(self.text)
            return True
        return False

    def submit(self) -> None:
        typed = self.text.strip()
        chosen = None
        if self.menu_open:
            # Enter in a filtered picker takes the row the caret is on, the way
            # vx's own menus do, not the fragment that was typed.
            matches = self.matches()
            if typed.startswith("/") and matches:
                chosen = matches[self.menu_selected].label
        text = typed
        self.text = ""
        self.cursor = 0
        self.menu_open = False
        if not text:
            return
        if chosen is not None:
            self.command(chosen)
            return
        if text.startswith("/"):
            self.command(text.split()[0].lower())
            return
        if self.live is not None:
            self.queued += 1
            self.add(blocks.Notice(topic="queued", body=text, detail="sent when the turn ends"))
            return
        self.add(user_turn(text))
        # A prompt is a request to see the answer, so it returns to the tail.
        self.viewport.bottom()
        self.start_turn()

    def command(self, label: str) -> None:
        found = next((cmd for cmd in COMMANDS if cmd.label == label), None)
        if found is None:
            self.add(
                blocks.Notice(topic="command", body=f"no fixture for {label}", tone="warning")
            )
            return
        action = found.action
        if action == "run":
            self.start_turn()
        elif action == "diff":
            self.add(*apply_patch(long_line=len(DIFF_LINES) % 2 == 0))
        elif action == "tools":
            self.add(tools_mixed_group())
        elif action == "usage":
            drawn = usage()
            if drawn is None:
                self.add(blocks.Notice(topic="usage", body="ctx's usage types are unavailable"))
            else:
                self.add(drawn)
        elif action == "notices":
            self.add(*notices())
        elif action == "sections":
            self.add(sections())
        elif action == "shell":
            self.add(shell_run())
        elif action == "theme":
            wanted = "fx-light" if theme.current().name != "fx-light" else "fx-dark"
            theme.apply_named(wanted)
            self.painter.reset()
            self.add(blocks.Notice(topic="theme", body=theme.current().name, tone="success"))
        elif action == "narrow":
            self._fixed_width = 60
            self.painter.reset()
        elif action == "wide":
            self._fixed_width = 120
            self.painter.reset()
        elif action == "clear":
            self.viewport.clear()
            self.add(welcome())
        elif action == "keys":
            self.add(keymap())
        elif action == "quit":
            self._running = False

    # -- painting ----------------------------------------------------------

    def footer(self, cols: int, height: int) -> tuple[list[str], tuple[int, int] | None]:
        if self.menu_open:
            matches = self.matches()
            rows = [footer.rule(cols)]
            composer, _row, _col = footer.composer_rows(
                self.text, self.cursor, cols, self.input_rows(cols)
            )
            rows.extend(composer)
            rows.append(footer.rule(cols))
            window = max(1, min(footer.MENU_MAX_ROWS, height - 10 - len(composer)))
            rows.extend(
                footer.menu_rows(
                    cols=cols,
                    items=menu_items(matches),
                    selected=self.menu_selected,
                    noun=f"Commands · {self.text}",
                    max_rows=window,
                )
            )
            rows.append(footer.rule(cols))
            rows.append(footer.menu_hint(cols=cols))
            return rows, None

        rows = [footer.rule(cols)]
        composer_start = len(rows)
        composer, caret_row, caret_col = footer.composer_rows(
            self.text, self.cursor, cols, self.input_rows(cols)
        )
        rows.extend(composer)
        rows.append(footer.rule(cols))
        right = ""
        if self.live is not None:
            right = footer.activity_note(
                "responding", self.live.started, time.monotonic(), self.queued
            )
        elif self.viewport.scroll and not self.viewport.unseen:
            noun = "line" if self.viewport.scroll == 1 else "lines"
            right = f"↑ {self.viewport.scroll} {noun} · pgdn or end"
        rows.append(
            footer.hint_row(
                cols=cols,
                permission=self.permission,
                model=self.model,
                effort=self.effort,
                context_used=self.context_used,
                context_total=self.context_total,
                workspace=self.workspace,
                branch=self.branch,
                right=right,
            )
        )
        return rows, (composer_start + caret_row, caret_col)

    def input_rows(self, cols: int) -> int:
        total = len(footer.wrap_input(self.text, cols))
        return max(1, min(MAX_INPUT_LINES, total))

    def draw(self) -> None:
        height, cols = self.size()
        foot, caret = self.footer(cols, height)
        foot = foot[-max(1, height - 1) :]
        area = max(1, height - len(foot))
        transcript = self.viewport.visible(cols, area)
        if self.viewport.unseen:
            transcript[-1] = footer.jump_to_latest_pill(cols)
        frame = (transcript + foot)[:height]
        if len(frame) < height:
            frame += [""] * (height - len(frame))
        cursor = None
        if caret is not None:
            cursor = (area + caret[0], caret[1])
        if os.environ.get("VX_DEBUG_FRAME"):
            import json

            with open(os.environ["VX_DEBUG_FRAME"], "a") as fh:
                fh.write(
                    json.dumps(
                        {
                            "rows": height,
                            "cols": cols,
                            "scroll": self.viewport.scroll,
                            "unseen": self.viewport.unseen,
                            "total": self.viewport.total(cols),
                            "expanded": blocks.expanded(),
                            "caret": list(cursor) if cursor else None,
                            "foot": len(foot),
                        }
                    )
                    + "\n"
                )
        payload = self.painter.paint(frame, cols, cursor)
        if payload or self._resized:
            self.term.write(payload)
            self._resized = False

    def run(self) -> None:
        self.draw()
        while self._running:
            data = self.term.read_available()
            if data:
                self.feed(data)
            self.tick()
            self.draw()
            time.sleep(0.03)


# ---------------------------------------------------------------------------
# one-shot rendering, for piping a frame into a bug report
# ---------------------------------------------------------------------------


def compose_static(
    blocks_in: list[blocks.Block], cols: int, height: int, *, footer_rows: bool
) -> list[str]:
    viewport = Viewport()
    for block in blocks_in:
        viewport.add(block)
    foot: list[str] = []
    if footer_rows:
        # A resting frame: the rule above the composer, the composer, the rule,
        # and the hint, with nothing running and nothing scrolled.
        text = "explain what changed in the viewport"
        composer, _row, _col = footer.composer_rows(text, len(text), cols, 1)
        foot = [
            footer.rule(cols),
            *composer,
            footer.rule(cols),
            footer.hint_row(
                cols=cols,
                permission="auto",
                model="example-model",
                effort="default",
                context_used=56_000,
                context_total=320_000,
                workspace=os.getcwd(),
                branch="main",
            ),
        ]
    foot = foot[-max(1, height - 1) :]
    area = max(1, height - len(foot))
    frame = (viewport.visible(cols, area) + foot)[:height]
    return frame + [""] * max(0, height - len(frame))


def ruler(cols: int) -> str:
    """A width ruler, so a padding bug is a measurement and not a squint."""
    tens = "".join(str((i // 10) % 10) if i % 10 == 0 else " " for i in range(cols))
    units = "".join(str(i % 10) for i in range(cols))
    return f"{tens}\n{units}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="A live vx session drawn from fixtures, with no model behind it."
    )
    parser.add_argument("-w", "--width", type=int, default=None, help="pin the width")
    parser.add_argument("-H", "--height", type=int, default=None, help="pin the height")
    parser.add_argument("--theme", default="fx-dark", help="fx-dark, fx-light or a ctx theme")
    parser.add_argument("--only", help="print one fixture, no screen")
    parser.add_argument("--list", action="store_true", help="list the fixtures and exit")
    parser.add_argument("--snapshot", action="store_true", help="print one frame and exit")
    parser.add_argument("--plain", action="store_true", help="strip ANSI (implies --snapshot)")
    parser.add_argument("--expanded", action="store_true", help="start with ctrl+o held")
    parser.add_argument(
        "--thinking-lines", type=int, default=None, help="rows a folded thought keeps"
    )
    parser.add_argument("--history", type=int, default=None, help="cap the scrollback rows")
    parser.add_argument("--ruler", action="store_true", help="print a column ruler")
    args = parser.parse_args()

    if args.list:
        print("\n".join(FIXTURES))
        return 0

    theme.apply_named(args.theme)
    blocks.set_expanded(args.expanded)
    if args.thinking_lines is not None:
        # The harness decides the budget, so a layout question never depends on
        # whatever ~/.ctx/config.yml happens to say.
        budget = args.thinking_lines

        def fixed_budget() -> int:
            return budget

        blocks.thinking_line_budget = fixed_budget

    cols = args.width or 100
    height = args.height or 32

    if args.only:
        chosen = build(args.only)
        if not chosen:
            print(f"{args.only}: nothing to draw", file=sys.stderr)
            return 1
        if args.ruler:
            print(ruler(cols))
        for block in chosen:
            for row in block.rows(cols):
                print(ansi.strip_ansi(row) if args.plain else row)
        return 0

    if args.snapshot or args.plain or not sys.stdout.isatty():
        rows = compose_static(opening(), cols, height, footer_rows=True)
        if args.ruler:
            print(ruler(cols))
        for row in rows:
            print(ansi.strip_ansi(row) if args.plain else row)
        return 0

    if args.width or args.height:
        # A pinned size still needs a terminal, so shrink the reported one.
        size = (args.height or 32, args.width or 100)

        def fixed_size(self: Terminal) -> tuple[int, int]:
            return size

        Terminal.size = fixed_size  # type: ignore[method-assign]

    with Terminal() as term:
        harness = Harness(term, history=args.history)
        harness.term = term
        with contextlib.suppress(KeyboardInterrupt):
            harness.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
