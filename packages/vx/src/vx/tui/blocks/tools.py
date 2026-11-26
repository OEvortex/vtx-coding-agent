"""Tool status and tool group transcript blocks."""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from vx.tui import ansi, markdown
from vx.tui import theme as _theme
from vx.tui.blocks.base import Block, _duration, _elapsed_label, _short, expanded
from vx.tui.cache import Revisioned

_TOOL_VERBS = {
    "bash": "Ran",
    "read": "Read",
    "write": "Wrote",
    "edit": "Edited",
    "apply_patch": "Edited",
    "grep": "Searched",
    "search": "Searched",
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
    "ask_user": "Asked",
}

#: fx's verb for what a failed call tried to do (failedVerb).
_FAILED_VERBS = {
    "read": "read",
    "write": "write",
    "edit": "edit",
    "apply_patch": "edit",
    "grep": "search",
    "search": "search",
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
    "search": "read",
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

#: Rows of diff a finished patch prints before "ctrl+o to view".


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

    def parts(self) -> tuple[Revisioned, ...]:
        # A group is its calls: append, remove and status changes all live on
        # the children, so the group's own revision never moves.
        return (*self.calls, *self.details)

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
    """fx's recap under a turn: ``Ran 2 commands · 7s``.

    No usage on the line: a turn's size belongs in the reply that paid for it,
    and a pair of counts under every turn was noise next to the recap's prose.
    """

    recap: str = ""
    seconds: float = 0.0
    kind: str = "notice"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        parts = [part for part in (self.recap, _duration(self.seconds)) if part]
        if not parts:
            return []
        return [ansi.clip_ellipsis(f"  {t.dim_style}{' · '.join(parts)}{ansi.RESET}", width)]


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
@dataclass
class SearchToolStatus(ToolRow):
    """The search tool: what was looked for, where, and what it cost.

    Two modes, one row. Content mode reads ``Searched 'x' · 3 matches in 2
    files · 25ms``; files mode reads ``Found '*.py' · 6 files · 17ms``. A rung
    other than ripgrep names itself, because "why was that slow" is a fair
    question with an answer.
    """

    name: str = "search"
    pattern: str = ""
    mode: str = "content"
    scope: str = ""
    glob: str = ""
    backend: str = ""
    matches: int = 0
    files: int = 0
    total: int = 0
    scanned: int = 0
    counts: tuple[tuple[str, int], ...] = ()
    error: str = ""
    status: str = "running"
    kind: str = "tool"

    @property
    def category(self) -> str:
        """Files mode lists names; content mode reads files."""
        return "list" if self.mode == "files" else "read"

    def duration(self) -> str:
        """Milliseconds under a second: `25ms` reads, `0.0s` looks broken."""
        if self.elapsed is None:
            return ""
        if self.elapsed < 1:
            return f" {ansi.dim()}· {round(self.elapsed * 1000)}ms{ansi.RESET}"
        return super().duration()

    def _where(self) -> str:
        if not self.scope or self.scope == ".":
            return ""
        return self.scope

    #: A row wider than this has its optional parts dropped, so the duration and
    #: the mark never get eaten by a long pattern. The detail rows say the rest.
    ROW_BUDGET = 62

    def body(self) -> str:
        t = _theme.current()
        if self.status == "error":
            reason = self.error or "no results"
            return (
                f"{t.red_style}Couldn't search{ansi.RESET} "
                f"{t.dim_style}· {_short(reason, 48)}{ansi.RESET}" + self.duration()
            )
        if self.status == "running":
            verb = "Finding" if self.mode == "files" else "Searching"
        elif self.status == "cancelled":
            verb = "Cancelled"
        else:
            verb = "Found" if self.mode == "files" else "Searched"

        count = ""
        if self.status in ("ok", "error", "cancelled"):
            # A single-file scope scans nothing, so "in 0 files" would be true
            # and useless; the row says plainly that there was nothing.
            scanned = f" in {self.scanned} files" if self.scanned else ""
            if self.mode == "files":
                noun = "file" if self.files == 1 else "files"
                count = f"{self.files} {noun}" if self.files else "no files"
                if self.total > self.files:
                    count = f"{self.files} of {self.total} {noun}"
            elif not self.matches:
                count = f"no matches{scanned}"
            else:
                noun = "match" if self.matches == 1 else "matches"
                shown = (
                    f"{self.matches} of {self.total} {noun}"
                    if self.total > self.matches
                    else f"{self.matches} {noun}"
                )
                count = f"{shown} in {self.files} {'file' if self.files == 1 else 'files'}"
        backdrop = ""
        primary = "fd" if self.mode == "files" else "rg"
        if self.backend and self.backend != primary and self.status != "running":
            backdrop = self.backend

        pattern = _short(self.pattern, 40)
        where = self._where() if self.status != "running" else ""
        glob = _short(self.glob, 20) if self.glob and self.mode != "files" else ""

        def shape(pat: str, scope: str, name: str) -> str:
            tail = " · ".join(part for part in (count, name) if part)
            lead = f"{verb} {pat}" if pat else verb
            middle = " · ".join(part for part in (scope, glob) if part)
            head = f"{lead} · {middle}" if middle else lead
            return f"{head} · {tail}" if tail else head

        candidates = [
            (pattern, where, backdrop),
            (pattern, "", backdrop),
            (pattern, "", ""),
            (_short(self.pattern, 22), "", ""),
        ]
        text = candidates[-1]
        for candidate in candidates:
            text = candidate
            if ansi.width(shape(*candidate)) <= self.ROW_BUDGET:
                break

        pat, scope, name = text
        out = f"{t.dim_style}{verb}{ansi.RESET}"
        if pat:
            out += f" {t.tag_style}{pat}{ansi.RESET}"
        if scope:
            out += f" {t.dim_style}· {scope}{ansi.RESET}"
        if glob:
            out += f" {t.dim_style}· {glob}{ansi.RESET}"
        if count:
            out += f" {t.dim_style}· {ansi.RESET}{count}"
        if name:
            out += f" {t.dim_style}· {name}{ansi.RESET}"
        return out + self.duration()


@dataclass
@dataclass
class SearchResults(Block):
    """A search's detail, drawn only when ctrl+o asks for it.

    A row already says how much was found, so nothing is printed under it by
    default: the transcript is not a pager, and a preview of the hits pushes
    the conversation off the screen. ctrl+o is the pager, and then a hit reads
    like a line out of a file -- the file's own gutter width, its own code.
    """

    counts: tuple[tuple[str, int], ...] = ()
    groups: tuple[tuple[str, tuple[tuple[int, str], ...]], ...] = ()
    files: tuple[str, ...] = ()
    more: int = 0
    kind: str = "detail"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        out: list[str] = []
        if len(self.counts) > 1:
            # The one thing a row cannot say: which alternative found nothing.
            parts = []
            for name, count in self.counts:
                label = f"{_short(name, 20)} {count}"
                parts.append(label if count else f"{t.dim_style}{label}{ansi.RESET}")
            out.append(f"  {' · '.join(parts)}")
        if expanded():
            out.extend(self._hits(width, t))
        return [ansi.clip_ellipsis(row, width) for row in out]

    def _hits(self, width: int, t: Any) -> list[str]:
        """The hits, one file at a time, under that file's own ruler."""
        out: list[str] = [
            f"  {t.dim_style}{_display_path(path)}{ansi.RESET}" for path in self.files
        ]
        for path, hits in self.groups:
            out.append(f"  {t.dim_style}{_display_path(path)}{ansi.RESET}")
            gutter = max((len(str(line)) for line, _text in hits), default=1)
            for line, text in hits:
                number = str(line).rjust(gutter)
                code = markdown.highlight_line(text, t) if text else ""
                out.append(f"  {t.dim_style}{number} │{ansi.RESET} {code}")
        if self.more:
            out.append(f"  {t.dim_style}+{self.more} more · raise max_results{ansi.RESET}")
        return out


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


#: Every tool status variant, for callers that hold a heterogeneous list.
type ToolStatusBlock = (
    ToolStatus
    | ReadToolStatus
    | BashToolStatus
    | ApplyPatchToolStatus
    | GrepToolStatus
    | SearchToolStatus
    | WebToolStatus
)
