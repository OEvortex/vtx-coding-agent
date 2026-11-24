"""Transcript blocks, drawn the way fx draws them.

Each block turns itself into styled rows at a given width. The viewport adds
the blank rows between blocks and pins the welcome banner, so blocks never
worry about neighbours.
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from vx.brand import ITALIC_BRAND
from vx.tui import ansi, markdown
from vx.tui import theme as _theme

#: fx's assistant gutter: two columns of breathing room on every reply row.
GUTTER = 2
#: The connected rail that marks a user prompt in the transcript.
USER_RAIL = "┃"

_NOTICE_GLYPHS = {
    "information": "i",
    "success": "✓",
    "warning": "!",
    "error": "✗",
    "cancelled": "⊘",
    "neutral": "*",
}

_TOOL_VERBS = {
    "bash": "Ran",
    "read": "Read",
    "write": "Wrote",
    "edit": "Edited",
    "apply_patch": "Edited",
    "grep": "Searched",
    "find": "Listed",
    "glob": "Listed",
    "ls": "Listed",
    "web_search": "Searched the web",
    "web_fetch": "Fetched",
    "skill": "Loaded skill",
    "subagent": "Ran subagent",
    "task": "Ran subagent",
    "codemode": "Ran code",
    "tool_search": "Found tools",
}

#: fx's verb for what a failed call tried to do (failedVerb).
_FAILED_VERBS = {
    "read": "read",
    "write": "write",
    "edit": "edit",
    "apply_patch": "edit",
    "grep": "search",
    "find": "match",
    "glob": "match",
    "ls": "match",
    "web_fetch": "fetch",
    "web_search": "search",
    "skill": "load",
}

#: fx's activity kinds per tool, which decide how a group's header names the
#: work it did (src/ui/transcript/tool_group_projection.zig, categoryIndex).
_TOOL_CATEGORIES = {
    "read": "read",
    "grep": "read",
    "write": "write",
    "skill": "read",
    "edit": "edit",
    "apply_patch": "edit",
    "find": "list",
    "glob": "list",
    "ls": "list",
    "bash": "command",
    "shell": "command",
    "subagent": "subagent",
    "task": "subagent",
    "web_search": "web_search",
    "web_fetch": "web_page",
}

#: Kinds of work a group header names, in the order a tie breaks. Edits lead,
#: since they are the change the group made (fx's WorkKind order).
_GROUP_KINDS = (
    "edits",
    "subagents",
    "commands",
    "web_pages",
    "web_searches",
    "reads",
    "lists",
    "others",
)

_KIND_OF_CATEGORY = {
    "read": "reads",
    "list": "lists",
    "write": "edits",
    "edit": "edits",
    "command": "commands",
    "subagent": "subagents",
    "web_page": "web_pages",
    "web_search": "web_searches",
}

#: The most kinds of work a header names before ", and more".
HEADER_MAX_PARTS = 3

#: Row budget for a `!cmd`'s folded output, mirroring fx's compact transcript.
OUTPUT_PREVIEW_ROWS = 5
#: Rows of diff a finished patch prints before "ctrl+o to view".
_expanded = False


def set_expanded(flag: bool) -> None:
    """ctrl+o unfolds a `!cmd`'s output, fx's full-transcript view."""
    global _expanded
    _expanded = flag


def expanded() -> bool:
    return _expanded


def _gutter(rows: list[str], width: int) -> list[str]:
    lead = " " * GUTTER
    return [lead + r if r else "" for r in rows]


@dataclass
class Block:
    kind: str = "notice"

    def rows(self, width: int) -> list[str]:
        raise NotImplementedError


# Type alias for all tool status block variants (defined later in this file)
type ToolStatusBlock = (
    ToolStatus
    | ReadToolStatus
    | BashToolStatus
    | ApplyPatchToolStatus
    | GrepToolStatus
    | WebToolStatus
)


@dataclass
class Welcome(Block):
    """fx's welcome line: bright brand, dim build label and hint."""

    version: str = ""
    kind: str = "welcome"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        label = f"v{self.version}" if self.version else ""
        rows = ansi.wrap(
            f"{t.subtitle_style}{ITALIC_BRAND}{ansi.RESET}"
            f"{t.dim_style} {label} · Run /help for commands{ansi.RESET}",
            width,
        )
        return rows


@dataclass
class UserTurn(Block):
    """fx's prompt card: a connected rail, bold text, no background."""

    text: str = ""
    #: What the prompt carried besides its words, such as an image chip.
    attachments: str = ""
    kind: str = "user"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        window = max(1, width - 2)
        lead = f"{t.user_card_marker_style}{USER_RAIL}{ansi.RESET} {ansi.BOLD}"
        body = ansi.markup_to_ansi(self.text)
        out: list[str] = []
        for logical in body.replace("\r\n", "\n").split("\n"):
            for chunk in ansi.wrap(logical, window) if logical else [""]:
                out.append(f"{lead}{chunk}{ansi.RESET}")
        if self.attachments:
            rail = f"{t.user_card_marker_style}{USER_RAIL}{ansi.RESET}"
            chip = f"{t.dim_style}⧉ {self.attachments}{ansi.RESET}"
            for chunk in ansi.wrap(chip, window) if window else [chip]:
                out.append(f"{rail} {chunk}")
        return out or [f"{lead}{ansi.RESET}"]


@dataclass
class Assistant(Block):
    """Streaming markdown reply, indented by fx's two-column gutter."""

    text: str = ""
    kind: str = "assistant"

    def rows(self, width: int) -> list[str]:
        body_width = max(1, width - GUTTER)
        rendered = markdown.render(self.text, body_width)
        if not rendered:
            return []
        return _gutter(rendered, width)


def thinking_line_budget() -> int | None:
    """How many rows a finished thought keeps, from ctx's ``ui.thinking_lines``.

    The budget counts *rendered* rows, not source lines: providers stream one
    paragraph, so a line-based budget kept the whole thought and never folded
    anything. ``"none"`` shows everything, a digit caps the rows and leaves a
    ``... (N more rows)`` tail.
    """
    from ctx.core.config import config

    setting = str(config.ui.thinking_lines)
    if setting == "none":
        return None
    try:
        return max(1, int(setting))
    except ValueError:
        return 2


@dataclass
class Thinking(Block):
    """Reasoning, drawn as a one-call group: ``● Thinking · 4.0s`` and its rows.

    Live, the thought streams under a ``│`` spine at full length. Once it ends
    it keeps ``ui.thinking_lines`` rendered rows under ``├`` and closes with
    ``└ ... (N more rows)``, so a finished turn has a predictable height and
    says how much it is not showing. A resumed session arrives already folded.
    """

    text: str = ""
    streaming: bool = True
    elapsed: float | None = None
    kind: str = "thinking"

    def _rows(self, width: int) -> list[str]:
        body_width = max(1, width - 4)
        out: list[str] = []
        for line in self.text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            with suppress(Exception):
                # ctx flattens the markup in folded thinking, so vx shows the
                # same words it does instead of raw ``**emphasis**``.
                from ctx.term.formatting import strip_markdown_for_collapsed_text

                line = strip_markdown_for_collapsed_text(line)
            out.extend(ansi.wrap(line, body_width))
        return out

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        head = f"{t.dim_style}● Thinking{ansi.RESET}"
        if self.elapsed is not None and not self.streaming:
            head += f"{t.dim_style} · {_elapsed_label(self.elapsed)}{ansi.RESET}"
        body = self._rows(width)
        if not body:
            return [head]

        if self.streaming:
            return [head] + [
                f"{t.dim_style}│{ansi.RESET} {t.dim_style}{row}{ansi.RESET}" for row in body
            ]

        budget = thinking_line_budget()
        shown = body if budget is None else body[:budget]
        hidden = len(body) - len(shown)
        out = [head]
        for index, row in enumerate(shown):
            # ``├`` while another row follows (or the tail will), ``└`` last.
            mark = "├" if hidden or index < len(shown) - 1 else "└"
            out.append(f"{t.dim_style}{mark}{ansi.RESET} {t.dim_style}{row}{ansi.RESET}")
        if hidden:
            noun = "row" if hidden == 1 else "rows"
            out.append(
                ansi.clip_ellipsis(f"{t.dim_style}└ ... ({hidden} more {noun}){ansi.RESET}", width)
            )
        return out


@dataclass
class HistoryFold(Block):
    """What a scrollback limit dropped: one row where the old transcript was.

    tmux grows a pane's history silently; a transcript that loses its own head
    should say so, or an old turn simply disappears with no trace.
    """

    dropped: int = 0
    kind: str = "notice"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        label = f"↑ {self.dropped:,} earlier rows dropped · /settings → history"
        return [f"  {t.dim_style}{ansi.clip_ellipsis(label, max(1, width - 2))}{ansi.RESET}"]


@dataclass
class Notice(Block):
    """``glyph topic: body`` with a dim detail row, as fx prints notices."""

    topic: str = ""
    body: str = ""
    detail: str = ""
    tone: str = "information"
    kind: str = "notice"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        label_style = {
            "warning": t.warning_style,
            "error": t.red_style,
            "success": t.green_style,
            "cancelled": t.dim_style,
        }.get(self.tone, t.system_notice_label_style)
        glyph = _NOTICE_GLYPHS.get(self.tone, "*")
        head = f"{label_style}{glyph}"
        if self.topic:
            head += f" {self.topic}:"
        head += f"{ansi.RESET}{t.system_notice_text_style}"
        text = head
        if self.body:
            text += f" {ansi.markup_to_ansi(self.body)}"
        text += ansi.RESET
        out = ansi.wrap(text, width)
        detail = self.detail.strip()
        if detail:
            for line in detail.split("\n"):
                for chunk in ansi.wrap(line, max(1, width - 2)):
                    out.append(f"  {t.dim_style}{chunk}{ansi.RESET}")
        return out


@dataclass
class ToolRow(Block):
    """One tool call's row, drawn as fx draws a group's calls.

    A call alone reads ``● Read src/app.py ✓``; inside a group it reads
    ``├ Read src/app.py``, and the last call of a group uses ``└``.
    """

    name: str = ""
    status: str = "running"  # running | ok | error | cancelled
    #: When the call finished, in the order calls that ran together returned.
    finished: int = 0
    elapsed: float | None = None
    kind: str = "tool"

    def body(self) -> str:
        raise NotImplementedError

    def glyph(self) -> str:
        return "●"

    def duration(self) -> str:
        if self.elapsed is None:
            return ""
        return f" {ansi.dim()}· {_elapsed_label(self.elapsed)}{ansi.RESET}"

    @property
    def category(self) -> str:
        return _TOOL_CATEGORIES.get(self.name, "other")

    def mark(self) -> str:
        t = _theme.current()
        if self.status == "ok":
            return f" {t.task_completed_open}✓{ansi.RESET}"
        # A failed call says how it ended in its own phrase ("Exited 2 …",
        # "Couldn't read …"), so fx gives it no ✗ as well.
        if self.status == "cancelled":
            return f" {t.dim_style}⊘{ansi.RESET}"
        return ""

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        return [_tool_row(self, width, f"{t.dim_style}{self.glyph()}{ansi.RESET} ")]

    def child_row(self, width: int, last: bool = False) -> str:
        t = _theme.current()
        lead = "└" if last else "├"
        # fx's tree rows carry no result mark: the phrase itself says how the
        # call went ("Exited 2 …", "Couldn't read …"), and the header's mark
        # already reports the group.
        return ansi.clip_ellipsis(f"{t.dim_style}{lead}{ansi.RESET} {self.body()}", width)

    def couldnt(self, verb: str, target: str = "") -> str:
        """fx's phrase for a failed call that is not a command: "Couldn't read x"."""
        t = _theme.current()
        rest = f" {target}" if target else ""
        return f"{t.red_style}Couldn't {verb}{rest}{ansi.RESET}"


@dataclass
class ToolStatus(ToolRow):
    """``● Ran zig build ✓``: one row per call, fx's transcript activity."""

    name: str = ""
    target: str = ""
    status: str = "running"  # running | ok | error | cancelled
    calls: int = 1
    kind: str = "tool"

    def _verb(self) -> str:
        return _TOOL_VERBS.get(self.name, self.name)

    def body(self) -> str:
        t = _theme.current()
        if self.status == "error":
            return self.couldnt(_FAILED_VERBS.get(self.name, "run"), self.target) + self.duration()
        if self.status == "running":
            label = "Running"
        elif self.status == "cancelled":
            label = "Cancelled"
        else:
            label = self._verb()
        out = f"{t.dim_style}{label}{ansi.RESET}"
        if self.target:
            out += f" {t.tag_style}{self.target}{ansi.RESET}"
        if self.calls > 1:
            out += f"{t.dim_style} ×{self.calls}{ansi.RESET}"
        return out + self.duration()


def _display_path(path: str) -> str:
    """A path as fx names it in a row: relative to the folder vx runs in."""
    if not path:
        return path
    with suppress(Exception):
        cwd = os.getcwd()
        absolute = os.path.abspath(path)
        if absolute == cwd:
            return "."
        if absolute.startswith(cwd.rstrip(os.sep) + os.sep):
            return os.path.relpath(absolute, cwd)
    return path


def _tool_row(block: ToolRow, width: int, lead: str) -> str:
    """``● Ran … ✓``: the body is clipped before the mark, never after it.

    Clipping the joined string would let a long command eat the ✓ or the
    duration, which is the part that says the call finished and how it ended.
    """
    mark = block.mark()
    room = max(8, width - ansi.width(lead) - ansi.width(mark))
    return f"{lead}{ansi.clip_ellipsis(block.body(), room)}{mark}"


def _status_of(call: ToolRow) -> str:
    return getattr(call, "status", "ok")


def _kind_groups(calls: list[ToolRow]) -> dict[str, list[ToolRow]]:
    grouped: dict[str, list[ToolRow]] = {name: [] for name in _GROUP_KINDS}
    for call in calls:
        grouped[_KIND_OF_CATEGORY.get(call.category, "others")].append(call)
    return grouped


def _kind_phrase(kind: str, calls: list[ToolRow]) -> str:
    """What the group did of ``kind``, such as "ran commands" (fx's
    workPhrase): no counts, since the rows show how much."""
    one = len(calls) == 1
    running = any(_status_of(call) == "running" for call in calls)
    if kind == "edits":
        writes_only = all(call.category == "write" for call in calls)
        if running:
            verb = "writing" if writes_only else "editing"
        else:
            verb = "wrote" if writes_only else "edited"
        return f"{verb} {'a file' if one else 'files'}"
    if kind == "subagents":
        return f"{'starting' if running else 'started'} {'a subagent' if one else 'subagents'}"
    if kind == "commands":
        return f"{'running' if running else 'ran'} {'a command' if one else 'commands'}"
    if kind == "web_pages":
        return "read a web page" if one else "read web pages"
    if kind == "web_searches":
        return "searched the web"
    if kind == "reads":
        return "read a file" if one else "read files"
    if kind == "lists":
        return "listed a file" if one else "listed files"
    return "used a tool" if one else "used tools"


def _group_parts(calls: list[ToolRow]) -> list[str]:
    """ "Read files, ran commands, and more": the header's parts, edits first
    and then the kind of work with the most calls."""
    grouped = _kind_groups(calls)
    ranked = [name for name in _GROUP_KINDS if grouped[name]]
    ranked.sort(key=lambda name: (name != "edits", -len(grouped[name])))
    parts = [_kind_phrase(name, grouped[name]) for name in ranked[:HEADER_MAX_PARTS]]
    if len(ranked) > HEADER_MAX_PARTS:
        parts.append("and more")
    if parts:
        parts[0] = parts[0][:1].upper() + parts[0][1:]
    return parts


def _group_mark(calls: list[ToolRow]) -> str:
    """Where the group's commands ended, as fx reports it.

    The mark speaks for the last command only: ✗ when it failed, ✓ once
    nothing in the group runs. A group with no commands has no mark for them
    to report, and earlier failures are counted beside it ("· 1 failed").
    """
    commands = [call for call in calls if call.category == "command"]
    if not commands:
        return ""
    t = _theme.current()
    # Calls run together, so "the last command" is the last one to return.
    latest = max(commands, key=lambda call: call.finished)
    last = _status_of(latest)
    if last == "error":
        return f" {t.red_style}✗{ansi.RESET}"
    if last in ("running", "cancelled"):
        return ""
    return f" {t.task_completed_open}✓{ansi.RESET}"


def _group_states(calls: list[ToolRow]) -> str:
    """ " · 1 failed · 1 cancelled": how the calls the mark does not speak for
    ended. The last command's own failure is the mark's to say."""
    t = _theme.current()
    out = ""
    commands = [call for call in calls if call.category == "command"]
    latest = max(commands, key=lambda call: call.finished) if commands else None
    failed = sum(_status_of(call) == "error" and call is not latest for call in commands)
    if failed:
        out += f"{t.dim_style} · {failed} failed{ansi.RESET}"
    cancelled = sum(_status_of(call) == "cancelled" for call in calls)
    if cancelled:
        out += f"{t.dim_style} · {cancelled} cancelled{ansi.RESET}"
    return out


def _group_header(calls: list[ToolRow], width: int) -> str:
    """The group's one-line header, clipped like fx: parts that do not fit
    drop from the end, and " …" says the header goes on."""
    t = _theme.current()
    parts = _group_parts(calls) or [f"{len(calls)} tool calls"]
    mark = _group_mark(calls)
    states = _group_states(calls)
    kept = len(parts)
    while kept > 0:
        text = parts[0] if kept == 1 else f"{parts[0]}, " + ", ".join(parts[1:kept])
        if kept < len(parts):
            text += " …"
        line = (
            f"{t.user_card_marker_style}●{ansi.RESET}"
            f"{t.dim_style} {text}{ansi.RESET}{mark}{states}"
        )
        if ansi.width(line) <= width:
            return line
        kept -= 1
    return ansi.clip_ellipsis(
        f"{t.user_card_marker_style}●{ansi.RESET}{t.dim_style} {parts[0]}{ansi.RESET}"
        f"{mark}{states}",
        width,
    )


@dataclass
class ToolGroup(Block):
    """A run of tool calls, drawn as fx draws a group.

    A group of one call is that call's own row, so nothing is said twice. A
    group of several gets a header naming what it did, then its calls as a
    tree: ``├ `` for each and ``└ `` for the last.
    """

    calls: list[ToolStatusBlock] = field(default_factory=list)
    details: list[Block] = field(default_factory=list)
    kind: str = "tool"

    def rows(self, width: int) -> list[str]:
        out: list[str] = []
        rows = _story_rows(list(self.calls))
        if len(self.calls) == 1:
            out.extend(rows[0].rows(width))
        elif rows:
            out.append(_group_header(list(self.calls), width))
            last = len(rows) - 1
            for index, call in enumerate(rows):
                out.append(call.child_row(width, last=index == last))
        for detail in self.details:
            out.extend(detail.rows(width))
        return out


def _story_rows(calls: list[ToolRow]) -> list[ToolRow]:
    """fx's rows for a group's calls: one row each, failures first.

    Nothing is combined. A merged row says what ran but not which command
    failed or how long each took, so every call keeps its own row and a
    failure sorts above the calls that passed.
    """
    failed = [call for call in calls if _status_of(call) == "error"]
    passed = [call for call in calls if _status_of(call) != "error"]
    return failed + passed


@dataclass
class TurnSummary(Block):
    """fx's recap under a turn: ``Ran 2 commands · 7s (↑30 ↓88)``."""

    recap: str = ""
    seconds: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    kind: str = "notice"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        parts = [part for part in (self.recap, _duration(self.seconds)) if part]
        if not parts:
            return []
        text = " · ".join(parts)
        if self.tokens_in or self.tokens_out:
            text += f" (↑{self.tokens_in} ↓{self.tokens_out})"
        return [ansi.clip_ellipsis(f"  {t.dim_style}{text}{ansi.RESET}", width)]


def _duration(seconds: float) -> str:
    total = round(seconds)
    if total < 60:
        return f"{total}s"
    minutes, rest = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {rest}s" if rest else f"{minutes}m"
    hours, rest = divmod(minutes, 60)
    return f"{hours}h {rest}m" if rest else f"{hours}h"


def _elapsed_label(seconds: float) -> str:
    """``1.4s`` under ten seconds, ``2m 5s`` above it, as tool rows read."""
    return f"{seconds:.1f}s" if seconds < 10 else _duration(seconds)


@dataclass
class ShellRun(Block):
    """A ``!cmd`` the user ran directly, echoed like fx echoes it."""

    command: str = ""
    output: str = ""
    done: bool = False
    success: bool = True
    send_to_llm: bool = False
    kind: str = "tool"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        prefix = "!!" if self.send_to_llm else "!"
        # The echo is one row by contract: a command longer than the terminal
        # must elide, never wrap the frame under the composer.
        echo = ansi.clip_ellipsis(self.command, max(1, width - ansi.width(prefix) - 1))
        out = [f"{t.dim_style}{prefix}{ansi.RESET} {ansi.BOLD}{echo}{ansi.RESET}"]
        if not self.done:
            return out
        lines = [ln for ln in self.output.replace("\r", "").split("\n") if ln.strip()]
        if _expanded:
            # Expanded: all lines with 4-space indent, no rail (matching fx)
            for line in lines:
                body = ansi.clip(line, max(1, width - 4))
                out.append(f"    {t.tool_stdout_style}{body}{ansi.RESET}")
        else:
            # Compact: up to OUTPUT_PREVIEW_ROWS with │ prefix, then fold hint
            shown = lines[:OUTPUT_PREVIEW_ROWS]
            for line in shown:
                body = ansi.clip(line, max(1, width - 2))
                out.append(f"{t.tool_stdout_style}│ {body}{ansi.RESET}")
            hidden = len(lines) - len(shown)
            if hidden > 0:
                noun = "line" if hidden == 1 else "lines"
                fold = f"│ … {hidden} {noun} more (ctrl+o to view)"
                out.append(f"{t.dim_style}{ansi.clip(fold, width)}{ansi.RESET}")
        if not self.success:
            suffix = "    exit failure" if _expanded else "│ exit failure"
            out.append(f"{t.dim_style}{suffix}{ansi.RESET}")
        return out


#: Heat levels, from a quiet day to the busiest, as fx's contribution grid draws
#: them: density rather than colour carries the intensity, so any palette works.
HEAT_GLYPHS = ("░", "▒", "▓", "█")
#: Weekday initials, Sunday first to match the columns the grid starts on.
DAY_INITIALS = ("s", "m", "t", "w", "t", "f", "s")
#: Rows that carry a weekday label: Monday, Wednesday and Friday.
DAY_LABELLED = (1, 3, 5)


def _heat_levels(values: list[int]) -> list[int]:
    """Three ascending cut points at the 50th, 75th and 90th percentile.

    Thresholds come from the data's own spread rather than fixed numbers, so a
    history of huge days and one of small days both show a gradient.
    """
    positives = sorted(v for v in values if v > 0)
    if not positives:
        return []
    return [
        positives[min(len(positives) - 1, int(len(positives) * fraction))]
        for fraction in (0.5, 0.75, 0.9)
    ]


def _heat_level(value: int, cuts: list[int]) -> int:
    level = 0
    for cut in cuts:
        if value >= cut:
            level += 1
    return min(level, len(HEAT_GLYPHS) - 1)


#: Card geometry, as ctx draws them: three summary cards in one row when the
#: terminal is wide enough for each to hold a total without clipping.
CARD_MIN_WIDTH = 28
CARD_MAX_WIDTH = 34
CARD_GAP = 2


def _usage_colors() -> Any:
    """ctx's palette, so ``/usage`` matches the reference byte for byte."""
    from ctx.core.config import config

    return config.ui.colors


def _usage_style(role: str, *, bold: bool = False) -> str:
    from vx.tui.theme import _fg

    with suppress(Exception):
        return _fg(getattr(_usage_colors(), role, ""), bold=bold)
    return "\x1b[1m" if bold else ""


def _total_line(usage: Any) -> str:
    """A compact total plus the exact count, so the arithmetic is checkable."""
    from ctx.term.formatting import format_tokens

    total = getattr(usage, "total_tokens", 0) or 0
    return f" {format_tokens(total)} · {total:,}"


def _usage_cells(title: str, usage: Any, inner: int, reset: str) -> list[str]:
    """The four inner rows of one summary card, each padded to ``inner``."""
    from ctx.term.formatting import format_tokens

    border = _usage_style("border")
    strong = _usage_style("fg", bold=True)
    quiet = _usage_style("dim")

    turns = getattr(usage, "turns", 0) or 0
    noun = "turn" if turns == 1 else "turns"
    lines = [
        (f" {_total_line(usage)}", strong),
        (f" {turns:,} {noun}", quiet),
        (
            f" ↑ {format_tokens(getattr(usage, 'input_tokens', 0) or 0)}"
            f"  ↓ {format_tokens(getattr(usage, 'output_tokens', 0) or 0)}"
            f"  ◈ {format_tokens(getattr(usage, 'cache_read_tokens', 0) or 0)}",
            quiet,
        ),
    ]
    out: list[str] = []
    for text, style in lines:
        text = ansi.clip(text, inner)
        fill = " " * max(0, inner - ansi.width(text))
        out.append(f"{border}│{reset}{style}{text}{reset}{fill}{border}│{reset}")
    top_title = ansi.clip(f"─ {title} ", inner)
    fill = "─" * max(0, inner - ansi.width(top_title))
    return [
        f"{border}╭{reset}{strong}{top_title}{reset}{border}{fill}╮{reset}",
        *out,
        f"{border}╰{'─' * inner}╯{reset}",
    ]


def _usage_cards(session: Any, report: Any, width: int) -> list[str]:
    """Session / last 30 days / lifetime, side by side or stacked."""
    reset = ansi.RESET
    summaries = (
        ("Session", session),
        ("Last 30 days", getattr(report, "last_30_days", None)),
        ("Lifetime", getattr(report, "lifetime", None)),
    )
    if all(usage is None for _, usage in summaries):
        return []
    summaries = tuple(
        (title, usage if usage is not None else _empty_usage()) for title, usage in summaries
    )

    if width >= CARD_MIN_WIDTH * 3 + CARD_GAP * 2:
        card_w = min(CARD_MAX_WIDTH, max(CARD_MIN_WIDTH, (width - CARD_GAP * 2) // 3))
        cards = [_usage_cells(title, usage, card_w - 2, reset) for title, usage in summaries]
        gap = " " * CARD_GAP
        return [gap.join(card[row] for card in cards) for row in range(len(cards[0]))]

    card_w = min(CARD_MAX_WIDTH, max(CARD_MIN_WIDTH, width - 2))
    out: list[str] = []
    for title, usage in summaries:
        if out:
            out.append("")
        out += _usage_cells(title, usage, card_w - 2, reset)
    return out


def _empty_usage() -> Any:
    from ctx.term.usage_stats import DailyUsage

    return DailyUsage()


def _usage_columns(report: Any, days: int, width: int) -> tuple[list[Any], Any]:
    end = getattr(report, "as_of", None) or date.today()
    start = end - timedelta(days=days - 1)
    start -= timedelta(days=(start.weekday() + 1) % 7)
    window_start = end - timedelta(days=days - 1)
    columns = [start + timedelta(days=7 * week) for week in range((end - start).days // 7 + 1)]
    fit = max(6, (width - 4 - 2) // 2)
    if len(columns) > fit:
        columns = columns[-fit:]
        window_start = max(window_start, columns[0])
    return columns, window_start


def _usage_heatmap(report: Any, days: int, width: int) -> list[str]:
    """The activity grid: weeks as columns, weekdays as rows."""
    end = getattr(report, "as_of", None) or date.today()
    columns, window_start = _usage_columns(report, days, width)
    day_map = getattr(report, "days", None) or {}
    cuts = _heat_levels([getattr(usage, "total_tokens", 0) for usage in day_map.values()])
    reset = ansi.RESET
    strong = _usage_style("fg", bold=True)
    quiet = _usage_style("dim")
    zero_shade = _usage_style("border")

    if days <= 31:
        subtitle = "Token activity · last 30 days"
    elif len(columns) >= 50:
        subtitle = "Token activity · last 12 months"
    else:
        months = max(1, round((end - columns[0]).days / 30.4))
        subtitle = f"Token activity · last {months} months"

    out = [f"{strong}{subtitle}{reset}", ""]

    # Month labels sit over the week each month starts in, when they fit.
    header = [" "] * (4 + len(columns) * 2)
    last_month = ""
    next_allowed = 4
    for index, week_start in enumerate(columns):
        month = ""
        for offset in range(7):
            visible = week_start + timedelta(days=offset)
            if window_start <= visible <= end:
                month = visible.strftime("%b")
                break
        if not month or month == last_month:
            continue
        position = 4 + index * 2
        if position < next_allowed:
            continue
        while len(header) < position + len(month):
            header.append(" ")
        for offset, char in enumerate(month):
            header[position + offset] = char
        next_allowed = position + len(month) + 1
        last_month = month
    out.append(f"{quiet}{''.join(header).rstrip()}{reset}")

    for weekday in range(7):
        label = f"  {DAY_INITIALS[weekday]} " if weekday in DAY_LABELLED else "    "
        cells: list[str] = []
        for week_start in columns:
            day = week_start + timedelta(days=weekday)
            if day < window_start or day > end:
                cells.append("  ")
                continue
            usage = day_map.get(day)
            total = getattr(usage, "total_tokens", 0) if usage is not None else 0
            level = _heat_level(total, cuts)
            shade = _heat_shade(level) if level else zero_shade
            cells.append(f"{shade}{HEAT_GLYPHS[level]}{reset} ")
        out.append(f"{quiet}{label}{reset}{''.join(cells)}".rstrip())

    legend = "".join(
        f"{_heat_shade(i) if i else zero_shade}{HEAT_GLYPHS[i]}{reset} "
        for i in range(len(HEAT_GLYPHS))
    )
    out.append(f"    {quiet}less {reset}{legend}{quiet}more{reset}")
    return out


def _heat_shade(level: int) -> str:
    """Blends of the background toward the accent, one per heat level."""
    from ctx.term.styles import _blend_hex

    from vx.tui.theme import _fg

    weights = (0.0, 0.25, 0.45, 0.7, 1.0)
    with suppress(Exception):
        colors = _usage_colors()
        return _fg(_blend_hex(colors.bg, colors.accent, overlay_weight=weights[level]))
    return _usage_style("accent")


@dataclass
class Usage(Block):
    """ctx's ``/usage``: summary cards over a year of token activity."""

    session: Any = None
    report: Any = None
    days: int = 365
    kind: str = "notice"

    def rows(self, width: int) -> list[str]:
        if self.report is None:
            return []
        reset = ansi.RESET
        out = [f"{_usage_style('fg', bold=True)}Token usage{reset}", ""]
        cards = _usage_cards(self.session, self.report, width)
        if cards:
            out += cards
            out.append("")
        out += _usage_heatmap(self.report, self.days, width)
        return out


@dataclass
class Sections(Block):
    """A titled report: sections of ``label / detail`` rows, as fx prints them."""

    title: str = ""
    sections: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    detail: str = ""
    kind: str = "notice"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        out: list[str] = []
        if self.title:
            out.append(
                f"{t.system_notice_label_style}"
                f"{ansi.clip_ellipsis(self.title, max(1, width - 2))}{ansi.RESET}"
            )
        for name, rows in self.sections:
            if out:
                out.append("")
            out.append(f"{t.dim_style}  {ansi.clip_ellipsis(name, max(1, width - 2))}{ansi.RESET}")
            label_width = max((ansi.width(label) for label, _ in rows), default=0)
            label_col = min(label_width + 2, max(12, width // 2))
            for label, row_detail in rows:
                row = f"    {ansi.clip_ellipsis(label, max(1, label_col - 2))}"
                if row_detail:
                    row = ansi.pad(row, 4 + label_col)
                    row += (
                        f"{t.dim_style}"
                        f"{ansi.clip_ellipsis(row_detail, max(1, width - 4 - label_col))}"
                        f"{ansi.RESET}"
                    )
                out.append(row)
        if self.detail:
            if out:
                out.append("")
            out.append(
                f"{t.dim_style}  {ansi.clip_ellipsis(self.detail, max(1, width - 2))}{ansi.RESET}"
            )
        return out


@dataclass
class ReadToolStatus(ToolRow):
    """Read tool: `F path:12-30` with line count."""

    name: str = "read"
    path: str = ""
    start_line: int = 0
    end_line: int = 0
    total_lines: int = 0
    is_dir: bool = False
    status: str = "running"
    kind: str = "tool"

    def glyph(self) -> str:
        return "●"

    def body(self) -> str:
        t = _theme.current()
        if self.status == "error":
            return self.couldnt("read", _display_path(self.path)) + self.duration()
        if self.status == "running":
            out = f"{t.dim_style}Reading{ansi.RESET}"
        elif self.status == "cancelled":
            out = f"{t.dim_style}Cancelled{ansi.RESET}"
        else:
            out = f"{t.dim_style}Read{ansi.RESET}"
        if self.path:
            out += f" {t.tag_style}{_display_path(self.path)}{ansi.RESET}"
        if self.start_line and self.end_line:
            if self.start_line == self.end_line:
                out += f" {t.dim_style}:L{self.start_line}{ansi.RESET}"
            else:
                out += f" {t.dim_style}:L{self.start_line}-{self.end_line}{ansi.RESET}"
        if self.total_lines and not self.is_dir:
            out += f"{t.dim_style} · {self.total_lines}L{ansi.RESET}"
        return out + self.duration()


@dataclass
class BashToolStatus(ToolRow):
    """Bash tool: ``● Ran zig build`` / ``● Exited 2 ls /nope`` (fx's row)."""

    name: str = "bash"
    command: str = ""
    exit_code: int | None = None
    status: str = "running"
    reason: str = ""
    kind: str = "tool"

    def body(self) -> str:
        t = _theme.current()
        if self.status == "running":
            label = "Running"
        elif self.status == "cancelled":
            label = "Cancelled"
        elif self.status == "error":
            # fx names how a command ended: "Exited 1" says more than "Ran".
            code = self.exit_code
            label = f"Exited {code}" if code else "Failed"
        else:
            label = "Ran"
        out = f"{t.dim_style}{label}{ansi.RESET}"
        command = " ".join(self.command.split())
        if command:
            out += f" {t.tool_stdout_style}{command}{ansi.RESET}"
        if self.reason:
            out += f"{t.dim_style} · {self.reason}{ansi.RESET}"
        return out + self.duration()


@dataclass
class DiffOutput(Block):
    """A patch's diff: a ruler gutter, word-wrapped, split when there is room.

    Narrow terminals get one column with the sign glued to the ruler
    (``206 │-``). Past :data:`DIFF_SPLIT_MIN_WIDTH` the same lines are paired
    into an old/new split, so the change reads left-to-right instead of top-to-
    bottom. Long lines wrap instead of being cut: a diff is the one place where
    the end of a line matters.
    """

    lines: list[tuple[str, str, str]] = field(default_factory=list)
    """List of (op, line_number, text) where op is '+', '-', or ' '."""
    path: str = ""
    """The file the lines belong to; empty when the block holds one file's diff."""
    #: Its own kind, so spacing can keep it welded under the row that edited.
    kind: str = "diff"

    def rows(self, width: int) -> list[str]:
        if not self.lines:
            return []
        t = _theme.current()
        out: list[str] = []
        if self.path:
            path = f"  {t.dim_style}± {_display_path(self.path)}{ansi.RESET}"
            out.append(ansi.clip_ellipsis(path, width))
        # A diff with nothing removed (a new file) would leave half the split
        # empty, so it keeps the full width and the one-column ruler.
        split = width >= DIFF_SPLIT_MIN_WIDTH and any(op == "-" for op, _ln, _x in self.lines)
        if split:
            out.extend(self._split(self.lines, width, t))
        else:
            out.extend(self._unified(self.lines, width, t))
        return out

    def _unified(self, lines: list[tuple[str, str, str]], width: int, t: Any) -> list[str]:
        """One column: ``  206 │- text``, wrapped under the code's own indent."""
        gw = _number_width(lines)
        # Two spaces, the number, " │" and the sign+space: the gutter is six
        # cells wider than the number, and a row one cell past `width` would
        # wrap the terminal and shift every row below it.
        cw = max(width - (gw + 6), 8)
        out: list[str] = []
        for op, ln, text in lines:
            if op == "~":
                out.append(_unchanged_row(ln, t))
                continue
            mark, style, bg = _diff_look(op, t)
            for index, chunk in enumerate(_code_chunks(_escape_text(text), cw)):
                if index == 0:
                    gutter = f"{t.dim_style}  {ln.rjust(gw)} │{ansi.RESET}{mark} "
                else:
                    gutter = f"{t.dim_style}  {' ' * gw} │{ansi.RESET}  "
                out.append(f"{gutter}{bg}{style}{chunk}{ansi.RESET}")
        return out

    def _split(self, lines: list[tuple[str, str, str]], width: int, t: Any) -> list[str]:
        """Two columns: the old file left, the new file right, aligned per change."""
        pairs = _split_pairs(lines)
        left_gw = _number_width([p for p in (left for left, _r in pairs if left) if p[0] != "~"])
        right_gw = _number_width(
            [p for p in (right for _l, right in pairs if right) if p[0] != "~"]
        )
        left_w = (width - len(_SPLIT_SEP)) // 2
        right_w = width - len(_SPLIT_SEP) - left_w
        left_cw = max(left_w - left_gw - 4, 8)
        right_cw = max(right_w - right_gw - 4, 8)
        dim = f"{t.dim_style}{_SPLIT_SEP}{ansi.RESET}"
        out: list[str] = []
        for left, right in pairs:
            # A run of unchanged lines spans both columns, so it breaks the
            # pairing instead of appearing twice.
            marker = left if left is not None else right
            if marker is not None and marker[0] == "~":
                out.append(_unchanged_row(marker[1], t))
                continue
            left_rows = _split_half(left, left_gw, left_cw, t)
            right_rows = _split_half(right, right_gw, right_cw, t)
            for index in range(max(len(left_rows), len(right_rows))):
                lrow = left_rows[index] if index < len(left_rows) else ""
                rrow = right_rows[index] if index < len(right_rows) else ""
                out.append(f"{_pad(lrow, left_w)}{dim}{rrow}")
        return out


#: Width at which a diff is paired into an old/new split instead of one column.
DIFF_SPLIT_MIN_WIDTH = 110
_SPLIT_SEP = " │ "


def _number_width(lines: list[tuple[str, str, str]]) -> int:
    return max((len(ln) for _op, ln, _text in lines if ln), default=1)


def _diff_look(op: str, t: Any) -> tuple[str, str, str]:
    """The sign, text style and background for one diff line."""
    if op == "+":
        return f"{t.diff_added_marker_style}+{ansi.RESET}", t.diff_added_style, "\x1b[48;5;22m"
    if op == "-":
        return f"{t.diff_removed_marker_style}-{ansi.RESET}", t.diff_removed_style, "\x1b[48;5;52m"
    return " ", t.diff_ctx_style, ""


def _unchanged_row(count: str, t: Any) -> str:
    """The elision row for a run of unchanged lines above, between or below."""
    lines = int(count) if count.isdigit() else 0
    noun = "line" if lines == 1 else "lines"
    return f"  {t.dim_style}⋯ {lines} {noun} unchanged{ansi.RESET}"


def _split_half(line: tuple[str, str, str] | None, gw: int, cw: int, t: Any) -> list[str]:
    """One side of a split row, wrapped to the column's text width."""
    if line is None:
        return []
    op, ln, text = line
    mark, style, bg = _diff_look(op, t)
    out: list[str] = []
    for index, chunk in enumerate(_code_chunks(_escape_text(text), cw)):
        if index == 0:
            gutter = f"{t.dim_style}{ln.rjust(gw)} │{ansi.RESET}{mark} "
        else:
            gutter = f"{t.dim_style}{' ' * gw} │{ansi.RESET}  "
        out.append(f"{gutter}{bg}{style}{chunk}{ansi.RESET}")
    return out


def _code_chunks(text: str, cw: int) -> list[str]:
    """Wrap code at ``cw``, hanging continuation rows under its own indent.

    Every row it returns fits in ``cw`` cells: an indent that ate the whole
    column, or a floor on the wrap width, would push the row past the terminal
    and wrap the frame itself.
    """
    body = text.lstrip(" ")
    indent = min(len(text) - len(body), max(cw // 2, 0), max(cw - 1, 0))
    if not body:
        return [""]
    budget = max(cw - indent, 1)
    chunks: list[str] = []
    for chunk in ansi.wrap(body, budget):
        # wrap() never splits a word that starts the row, so a long identifier
        # can still come back wider than the column; a diff row may not be.
        chunks.extend(ansi.hard_wrap(chunk, budget) if ansi.width(chunk) > budget else [chunk])
    hang = " " * indent
    return [hang + chunks[0], *(hang + chunk for chunk in chunks[1:])]


def _pad(text: str, col_w: int) -> str:
    """Pad styled text out to ``col_w`` cells so the divider stays straight."""
    return text + " " * max(col_w - ansi.width(text), 0)


def _split_pairs(
    lines: list[tuple[str, str, str]],
) -> list[tuple[tuple[str, str, str] | None, tuple[str, str, str] | None]]:
    """Pair removed lines with added ones so the split columns line up."""
    pairs: list[tuple[tuple[str, str, str] | None, tuple[str, str, str] | None]] = []
    index = 0
    total = len(lines)
    while index < total:
        op, _ln, _text = lines[index]
        if op == "~":
            # A run of unchanged lines belongs to neither column.
            pairs.append((lines[index], None))
            index += 1
            continue
        if op == " ":
            pairs.append((lines[index], lines[index]))
            index += 1
            continue
        removed: list[tuple[str, str, str]] = []
        added: list[tuple[str, str, str]] = []
        while index < total and lines[index][0] == "-":
            removed.append(lines[index])
            index += 1
        while index < total and lines[index][0] == "+":
            added.append(lines[index])
            index += 1
        for slot in range(max(len(removed), len(added))):
            left = removed[slot] if slot < len(removed) else None
            right = added[slot] if slot < len(added) else None
            pairs.append((left, right))
        if not removed and not added:
            # A lone '+' or '-' run not followed by its pair: keep the line.
            pairs.append((lines[index], None))
            index += 1
    return pairs


def _escape_text(text: str) -> str:
    """Strip trailing whitespace and control chars from diff text."""
    return text.rstrip()


@dataclass
class ApplyPatchToolStatus(ToolRow):
    """apply_patch tool: ``● Edited path +3 / -1`` — one row, never a diff.

    A call that touched several files says how many, because naming one of them
    would claim the others were untouched.
    """

    name: str = "apply_patch"
    path: str = ""
    files: int = 0
    added: int = 0
    removed: int = 0
    status: str = "running"
    kind: str = "tool"

    def body(self) -> str:
        t = _theme.current()
        if self.status == "error":
            return self.couldnt("edit", _display_path(self.path)) + self.duration()
        if self.status == "running":
            label = "Editing"
        elif self.status == "cancelled":
            label = "Cancelled"
        else:
            label = _TOOL_VERBS.get(self.name, self.name)
        out = f"{t.dim_style}{label}{ansi.RESET}"
        if self.files > 1:
            noun = "files" if self.files != 1 else "file"
            out += f" {t.tool_stdout_style}{self.files} {noun}{ansi.RESET}"
        elif self.path:
            out += f" {t.tool_stdout_style}{_display_path(self.path)}{ansi.RESET}"
        if self.status not in ("running", "cancelled"):
            # fx-style diff stats: +N / -M in dim
            if self.added > 0 and self.removed > 0:
                out += f" {t.dim_style}+{self.added}{ansi.RESET}"
                out += f" {t.dim_style}/{ansi.RESET}"
                out += f" {t.dim_style}-{self.removed}{ansi.RESET}"
            elif self.added > 0:
                out += f" {t.dim_style}+{self.added}{ansi.RESET}"
            elif self.removed > 0:
                out += f" {t.dim_style}-{self.removed}{ansi.RESET}"
        return out + self.duration()


@dataclass
class GrepToolStatus(ToolRow):
    """grep tool: `? pattern in path` with match count."""

    name: str = "grep"
    pattern: str = ""
    path: str = ""
    match_count: int = 0
    status: str = "running"
    kind: str = "tool"

    def glyph(self) -> str:
        return "●"

    def body(self) -> str:
        t = _theme.current()
        if self.status == "error":
            return self.couldnt("search", self.pattern) + self.duration()
        if self.status == "running":
            out = f"{t.dim_style}Searching{ansi.RESET}"
        elif self.status == "cancelled":
            out = f"{t.dim_style}Cancelled{ansi.RESET}"
        else:
            out = f"{t.dim_style}Searched{ansi.RESET}"
        if self.pattern:
            out += f" {t.tag_style}{self.pattern}{ansi.RESET}"
        if self.status != "running" and self.path:
            path = _display_path(self.path)
            out += f" {t.dim_style}in{ansi.RESET} {t.tag_style}{path}{ansi.RESET}"
        if self.status != "running" and self.match_count:
            noun = "match" if self.match_count == 1 else "matches"
            out += f" {t.dim_style}· {self.match_count} {noun}{ansi.RESET}"
        return out + self.duration()


@dataclass
class WebToolStatus(ToolRow):
    """The web tool: `Searched web query` or `Fetched url`."""

    name: str = "web"
    query: str = ""
    url: str = ""
    mode: str = "search"
    result_count: int = 0
    status: str = "running"
    kind: str = "tool"

    def glyph(self) -> str:
        return "●"

    def body(self) -> str:
        t = _theme.current()
        is_search = self.mode != "fetch" and self.name != "web_fetch"
        if self.status == "running":
            out = f"{t.dim_style}{'Searching web' if is_search else 'Fetching'}{ansi.RESET}"
        elif self.status == "cancelled":
            out = f"{t.dim_style}Cancelled{ansi.RESET}"
        else:
            out = f"{t.dim_style}{'Searched web' if is_search else 'Fetched'}{ansi.RESET}"
        target = self.query or self.url
        if target:
            out += f" {t.tag_style}{_short(target, 50)}{ansi.RESET}"
        if self.status != "running" and self.result_count:
            noun = "result" if self.result_count == 1 else "results"
            out += f" {t.dim_style}· {self.result_count} {noun}{ansi.RESET}"
        return out + self.duration()


def _short(text: str, limit: int = 70) -> str:
    """Truncate text to *limit* chars, adding ellipsis if needed."""
    flat = " ".join(str(text).split())
    return flat[: limit - 1] + "…" if len(flat) > limit else flat


def spacing(previous: str | None, kind: str) -> int:
    """Blank rows fx leaves between two transcript blocks.

    Every block is its own thought, so each one gets a row of air above it —
    including a tool group, which would otherwise sit welded to the reply that
    introduced it and to the group that followed it. A patch's diff is the one
    exception: it is the other half of the row above it, not a new thought.
    """
    if previous is None:
        return 0
    if kind == "diff":
        return 0
    return 1
