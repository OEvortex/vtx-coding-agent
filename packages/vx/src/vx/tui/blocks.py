"""Transcript blocks, drawn the way fx draws them.

Each block turns itself into styled rows at a given width. The viewport adds
the blank rows between blocks and pins the welcome banner, so blocks never
worry about neighbours.
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass, field

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
    "subagent": "Ran subagent",
    "task": "Ran subagent",
    "skill": "Loaded skill",
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
    "skill": "install",
}

#: fx's activity kinds per tool, which decide how a group's header names the
#: work it did (src/ui/transcript/tool_group_projection.zig, categoryIndex).
_TOOL_CATEGORIES = {
    "read": "read",
    "grep": "read",
    "write": "write",
    "skill": "write",
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

#: Row budget for a tool's folded output, mirroring fx's compact transcript.
OUTPUT_PREVIEW_ROWS = 5
OUTPUT_EXPANDED_ROWS = 60

_expanded = False


def set_expanded(flag: bool) -> None:
    """ctrl+o unfolds every tool output, fx's full-transcript view."""
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


@dataclass
class Thinking(Block):
    text: str = ""
    kind: str = "assistant"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        body_width = max(1, width - GUTTER)
        rendered = ansi.wrap(self.text.strip(), body_width)[:12]
        out = [f"  {t.dim_style}{ansi.ITALIC}thinking{ansi.ITALIC_OFF}{ansi.RESET}"]
        out.extend(
            f"  {t.dim_style}{ansi.ITALIC}{r}{ansi.ITALIC_OFF}{ansi.RESET}" for r in rendered
        )
        return out


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
    kind: str = "tool"

    def body(self) -> str:
        raise NotImplementedError

    def glyph(self) -> str:
        return "●"

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
            return self.couldnt(_FAILED_VERBS.get(self.name, "run"), self.target)
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
        return out


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
    return ansi.clip_ellipsis(f"{lead}{block.body()}{block.mark()}", width)


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
        if self.details and len(rows) > 1:
            out.append("")
        for detail in self.details:
            out.extend(detail.rows(width))
        return out


def _story_rows(calls: list[ToolRow]) -> list[ToolRow]:
    """fx's rows for a group's calls, combining the work that passed.

    Commands that ran one after another and did not fail share one row that
    names their steps (``Ran echo, cat``), and reads of files share one that
    names what they read, since nothing separates them but the names.
    """
    rows: list[ToolRow] = []
    run: list[ToolRow] = []
    kind = ""

    def flush() -> None:
        nonlocal run, kind
        if len(run) > 1:
            if kind == "read":
                rows.append(CombinedReads(calls=list(run)))
            else:
                rows.append(CombinedCommands(calls=list(run)))
        elif run:
            rows.append(run[0])
        run = []
        kind = ""

    for call in calls:
        mergeable = _status_of(call) == "ok" and isinstance(call, (BashToolStatus, ReadToolStatus))
        if mergeable:
            if run and kind != call.category:
                flush()
            kind = call.category
            run.append(call)
            continue
        flush()
        rows.append(call)
    flush()
    # fx shows the calls that failed before the ones that passed: what went
    # wrong is what a reader looks for.
    failed = [row for row in rows if _status_of(row) == "error"]
    return failed + [row for row in rows if _status_of(row) != "error"]


@dataclass
class CombinedReads(ToolRow):
    """One row for the files a group read, named once each."""

    calls: list[ToolRow] = field(default_factory=list)
    name: str = "read"
    status: str = "ok"
    kind: str = "tool"

    @property
    def category(self) -> str:
        return "read"

    def body(self) -> str:
        t = _theme.current()
        names: list[str] = []
        for call in self.calls:
            path = _display_path(getattr(call, "path", ""))
            if path and path not in names:
                names.append(path)
        kept = names[:SHORT_COMMAND_MAX_STEPS]
        more = len(names) - len(kept)
        text = ", ".join(kept) if kept else f"{len(self.calls)} files"
        if more > 0:
            text += f" +{more} more"
        return f"{t.dim_style}Read{ansi.RESET} {t.tag_style}{text}{ansi.RESET}"


@dataclass
class CombinedCommands(ToolRow):
    """One row for the commands that passed, naming each one's steps."""

    calls: list[ToolRow] = field(default_factory=list)
    name: str = "bash"
    status: str = "ok"
    kind: str = "tool"

    def body(self) -> str:
        t = _theme.current()
        steps: list[str] = []
        for call in self.calls:
            command = getattr(call, "command", "")
            if not command:
                continue
            steps.extend(_command_steps(command) or [_short(command, 40)])
        kept = steps[:SHORT_COMMAND_MAX_STEPS]
        more = len(steps) - len(kept)
        text = ", ".join(kept) if kept else f"{len(self.calls)} commands"
        if more > 0:
            text += f" +{more} more"
        return f"{t.dim_style}Ran{ansi.RESET} {t.tool_stdout_style}{text}{ansi.RESET}"


@dataclass
class ToolOutput(Block):
    """A call's output while it runs: fx indents it two spaces, no rail."""

    lines: list[str] = field(default_factory=list)
    buffer: str = ""
    hidden: int = 0
    kind: str = "tool"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        out: list[str] = []
        limit = OUTPUT_EXPANDED_ROWS if _expanded else OUTPUT_PREVIEW_ROWS
        shown = self.lines[:limit]
        for line in shown:
            chunk = ansi.clip(line.rstrip(), max(1, width - 2))
            out.append(f"  {t.tool_stdout_style}{chunk.rstrip()}{ansi.RESET}")
        hidden = self.hidden + max(0, len(self.lines) - limit)
        if hidden > 0:
            hint = "ctrl+o to collapse" if _expanded else "ctrl+o to view"
            out.append(f"  {t.dim_style}… {hidden} more lines ({hint}){ansi.RESET}")
        return out


#: How many of a call's output lines the compact transcript keeps. fx prints a
#: result whole, so the fold only takes over for output nobody reads.
RESULT_PREVIEW_ROWS = 20
#: How many ctrl+o unfolds.
RESULT_EXPANDED_ROWS = 200


@dataclass
class ToolResult(Block):
    """What a finished call returned, drawn as fx draws it.

    fx prints the target on a line of its own, then the content in a framed
    code panel: ``─ text ────``, the lines, and a closing rule.
    """

    label: str = ""
    lines: list[str] = field(default_factory=list)
    language: str = "text"
    kind: str = "tool"

    def rows(self, width: int) -> list[str]:
        if not self.lines and not self.label:
            return []
        t = _theme.current()
        out: list[str] = []
        if self.label:
            label = ansi.clip(self.label, max(1, width - 5))
            out.append(f"  {t.dim_style}•{ansi.RESET} {t.tag_style}{label}{ansi.RESET}:")
            out.append("")
        limit = RESULT_EXPANDED_ROWS if _expanded else RESULT_PREVIEW_ROWS
        shown = self.lines[:limit]
        hidden = len(self.lines) - len(shown)
        body = list(shown)
        if hidden > 0:
            hint = "ctrl+o to collapse" if _expanded else "ctrl+o to view"
            body.append(f"… {hidden} more lines ({hint})")
        for row in markdown.code_panel(body, self.language, max(4, width - 2)):
            out.append(f"  {row}")
        return out


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
        out = [f"{t.dim_style}{prefix}{ansi.RESET} {ansi.BOLD}{self.command}{ansi.RESET}"]
        if self.done:
            lines = [ln for ln in self.output.replace("\r", "").split("\n") if ln.strip()]
            limit = OUTPUT_EXPANDED_ROWS if _expanded else OUTPUT_PREVIEW_ROWS
            for line in lines[:limit]:
                body = ansi.clip(line, max(1, width - 2))
                out.append(f"{t.tool_stdout_style}│ {body}{ansi.RESET}")
            hidden = len(lines) - limit
            if hidden > 0:
                hint = "ctrl+o to collapse" if _expanded else "ctrl+o to view"
                out.append(f"{t.dim_style}│ … {hidden} more lines ({hint}){ansi.RESET}")
            if not self.success:
                out.append(f"{t.dim_style}│ exit failure{ansi.RESET}")
        return out


@dataclass
class DiffSummary(Block):
    files: int = 0
    added: int = 0
    removed: int = 0
    kind: str = "notice"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        noun = "file" if self.files == 1 else "files"
        text = f"± {self.files} {noun} +{self.added} -{self.removed}"
        return [f"{t.dim_style}{ansi.clip(text, width)}{ansi.RESET}"]


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
            return self.couldnt("read", _display_path(self.path))
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
        return out


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
        command = self.command if self.status == "running" else _short_command(self.command)
        if command:
            out += f" {t.tool_stdout_style}{command}{ansi.RESET}"
        if self.reason:
            out += f"{t.dim_style} · {self.reason}{ansi.RESET}"
        return out


#: fx's names for the programs it shortens to their own name (plain_programs).
_PLAIN_PROGRAMS = {
    "echo", "printf", "cat", "ls", "grep", "rg", "find", "fd", "sed", "awk", "head",
    "tail", "sleep", "rm", "cp", "mv", "mkdir", "rmdir", "touch", "chmod",
    "chown", "ln", "wc", "sort", "uniq", "cut", "tr", "tee", "xargs", "which",
    "pwd", "date", "curl", "wget", "jq", "diff", "stat", "du", "df", "ps",
    "kill", "pkill", "pgrep", "open", "tar", "unzip", "zip", "ssh", "scp",
    "rsync", "file", "basename", "dirname", "realpath", "readlink", "seq",
    "watch", "uname", "whoami",
}  # fmt: skip

#: Steps that only change the shell's own state, so they name no work.
_SHELL_SETUP = {
    "cd", "pushd", "popd", "export", "set", "unset", "source", ".", "true", ":",
    "exit", "wait", "trap", "shopt", "alias", "ulimit", "umask", "[", "[[", "test",
}  # fmt: skip

#: Shell words that lead into the command after them.
_LEADING_KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!"}

#: Shell words whose whole step runs no program of its own, such as a loop's
#: header or its end.
_STEP_KEYWORDS = {"for", "select", "case", "esac", "done", "fi", "in", "function"}

#: Programs that run the program after them.
_WRAPPERS = {"sudo", "time", "nohup", "exec", "command", "env", "builtin", "nice", "timeout"}

#: Programs that only reshape the output of the step before a pipe.
_OUTPUT_FILTERS = {
    "head", "tail", "grep", "rg", "egrep", "fgrep", "jq", "sed", "awk", "sort",
    "uniq", "wc", "cut", "tr", "tee", "less", "more", "cat", "column", "fold", "nl",
}  # fmt: skip

#: A command at most this long keeps its own text rather than its steps.
SHORT_COMMAND_MAX_LEN = 60
#: The most steps a short command names before " +N more".
SHORT_COMMAND_MAX_STEPS = 3


def _short_command(command: str) -> str:
    """fx's short form of a command: a compound command is named by its steps.

    ``cd desktop && npm ci --no-audit && npm run build`` reads as
    ``npm ci, npm run build``, while a one-step command at most
    ``SHORT_COMMAND_MAX_LEN`` long keeps its own text.
    """
    text = " ".join(command.split())
    steps = _command_steps(text)
    if not steps:
        return ansi.clip_ellipsis(text, SHORT_COMMAND_MAX_LEN)
    if len(_command_steps(text, raw=True)) <= 1 and len(text) <= SHORT_COMMAND_MAX_LEN:
        return text
    if len(steps) == 1 and (steps[0] == text or len(steps[0]) >= len(text)):
        return ansi.clip_ellipsis(text, SHORT_COMMAND_MAX_LEN)
    kept = steps[:SHORT_COMMAND_MAX_STEPS]
    more = len(steps) - len(kept)
    out = ", ".join(kept)
    if more > 0:
        out += f" +{more} more"
    return ansi.clip_ellipsis(out, SHORT_COMMAND_MAX_LEN)


def _command_steps(command: str, *, raw: bool = False) -> list[str]:
    """The steps a shell command runs, by program and subcommand.

    Ported in spirit from fx's ``shortCommandSteps``: quotes are respected,
    wrapper programs, leading keywords, environment assignments and shell
    setup are skipped, and words after a plain program are its arguments.
    """
    steps: list[str] = []
    count = 0
    for tokens, after_pipe in _split_steps(command):
        counted, step = _step_name(tokens, after_pipe)
        if not counted:
            continue
        count += 1
        if raw:
            steps.append(step or "")
            continue
        if step is None:
            continue
        if not steps or steps[-1] != step:
            steps.append(step)
    return steps


def _step_name(tokens: list[tuple[str, bool]], after_pipe: bool) -> tuple[bool, str | None]:
    """One step's short name, and whether the step counts as work at all."""
    program = ""
    words: list[str] = []
    skip_next = False
    for value, quoted in tokens:
        if not program:
            if not quoted and _is_assignment(value):
                continue
            if not quoted and value in _LEADING_KEYWORDS:
                continue
            if not quoted and value in _WRAPPERS:
                skip_next = False
                words = ["\0wrapper"]
                continue
            if words == ["\0wrapper"]:
                if value.startswith("-") or value[:1].isdigit():
                    continue
                words = []
            if not quoted and value in _SHELL_SETUP:
                return True, None
            if not quoted and value in _STEP_KEYWORDS:
                return True, None
            program = _program_name(value)
            if program in _PLAIN_PROGRAMS:
                break
            if skip_next:
                skip_next = False
            continue
        if skip_next:
            skip_next = False
            continue
        if program == "git" and value in ("-C", "-c"):
            skip_next = True
            continue
        if _is_word(value, quoted):
            words.append(value)
            if len(words) == 2:
                break
            continue
        break
    if not program:
        return False, None
    if after_pipe and program in _OUTPUT_FILTERS:
        return True, None
    return True, " ".join([program, *words])


def _program_name(value: str) -> str:
    """A program as the short form names it: its base name, or ./script."""
    if value.startswith("./"):
        return value.split("/", 2)[1] if value.count("/") == 1 else value
    return value.rsplit("/", 1)[-1]


def _is_word(value: str, quoted: bool) -> bool:
    """Whether a token is a subcommand word rather than a flag or a path."""
    if quoted or not value or not value[0].islower():
        return False
    return all(ch.islower() or ch.isdigit() or ch in "-_:." for ch in value)


def _is_assignment(value: str) -> bool:
    """Whether a token is a ``NAME=value`` environment assignment."""
    name, sep, _ = value.partition("=")
    return bool(sep) and bool(name) and all(ch == "_" or ch.isalnum() for ch in name)


def _split_steps(command: str) -> list[tuple[list[tuple[str, bool]], bool]]:
    """Shell words split at ``&&``, ``||``, ``;``, ``|`` and newlines."""
    steps: list[tuple[list[tuple[str, bool]], bool]] = []
    tokens: list[tuple[str, bool]] = []
    word: list[str] = []
    quoted = False
    open_quote = ""
    after_pipe = False
    index = 0

    def flush_word() -> None:
        nonlocal word, quoted
        if word:
            tokens.append(("".join(word), quoted))
            word = []
            quoted = False

    def flush_step(next_after_pipe: bool = False) -> None:
        nonlocal tokens, after_pipe
        flush_word()
        if tokens:
            steps.append((tokens, after_pipe))
        tokens = []
        after_pipe = next_after_pipe

    while index < len(command):
        ch = command[index]
        if open_quote:
            if ch == open_quote:
                open_quote = ""
            else:
                word.append(ch)
            index += 1
            continue
        if ch in "'\"":
            open_quote = ch
            quoted = True
            index += 1
            continue
        if command.startswith("&&", index) or command.startswith("||", index):
            flush_step(next_after_pipe=command.startswith("||", index))
            index += 2
            continue
        if ch in ";|\n&":
            flush_step(next_after_pipe=ch == "|")
            index += 1
            continue
        if ch.isspace():
            flush_word()
            index += 1
            continue
        word.append(ch)
        index += 1
    flush_step()
    return steps


@dataclass
class DiffOutput(Block):
    """fx-style diff preview: ``  │ N + text`` with colored markers.

    fx keeps diff blocks out of the compact transcript and shows them in the
    full one, so this renders only while ctrl+o has unfolded the transcript.
    """

    lines: list[tuple[str, str, str]] = field(default_factory=list)
    """List of (op, line_number, text) where op is '+', '-', or ' '."""
    kind: str = "tool"

    def rows(self, width: int) -> list[str]:
        if not self.lines or not _expanded:
            return []
        t = _theme.current()
        # Calculate gutter width from max line number
        max_ln = 0
        for _op, ln, _text in self.lines:
            if ln:
                max_ln = max(max_ln, len(ln))
        gw = max(max_ln, 1)
        out: list[str] = []
        for op, ln, text in self.lines:
            # Build the gutter: "  │ " + line number padded + " "
            gutter = f"  │ {ln.rjust(gw)} " if ln else f"  │ {' ' * gw} "
            # Choose style based on op — use marker style for the +/- character,
            # text style for the line content, and a subtle background highlight
            if op == "+":
                marker = f"{t.diff_added_marker_style}+{ansi.RESET}"
                line_style = t.diff_added_style
                bg = "\x1b[48;5;22m"  # dim green bg
            elif op == "-":
                marker = f"{t.diff_removed_marker_style}-{ansi.RESET}"
                line_style = t.diff_removed_style
                bg = "\x1b[48;5;52m"  # dim red bg
            else:
                marker = " "
                line_style = t.diff_ctx_style
                bg = ""
            # Format: gutter + marker + " " + text (with background on added/removed)
            line = (
                f"{t.dim_style}{gutter}{ansi.RESET}{marker} "
                f"{bg}{line_style}{_escape_text(text)}{ansi.RESET}"
            )
            out.append(ansi.clip_ellipsis(line, width))
        return out


def _escape_text(text: str) -> str:
    """Strip trailing whitespace and control chars from diff text."""
    return text.rstrip()


@dataclass
class ApplyPatchToolStatus(ToolRow):
    """apply_patch tool: ``● Edited path +3 -1`` — fx's compact one-row status."""

    name: str = "apply_patch"
    path: str = ""
    added: int = 0
    removed: int = 0
    ops_count: int = 0
    status: str = "running"
    kind: str = "tool"
    diff_lines: list[tuple[str, str, str]] = field(default_factory=list)
    """Diff preview lines for DiffOutput: (op, line_number, text)."""

    def body(self) -> str:
        t = _theme.current()
        if self.status == "error":
            return self.couldnt("edit", _display_path(self.path))
        if self.status == "running":
            label = "Editing"
        elif self.status == "cancelled":
            label = "Cancelled"
        else:
            label = _TOOL_VERBS.get(self.name, self.name)
        out = f"{t.dim_style}{label}{ansi.RESET}"
        if self.path:
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
        return out


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
            return self.couldnt("search", self.pattern)
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
        return out


@dataclass
class WebToolStatus(ToolRow):
    """web/web_search tool: `W query` or `L url`."""

    name: str = "web"
    query: str = ""
    url: str = ""
    result_count: int = 0
    status: str = "running"
    kind: str = "tool"

    def glyph(self) -> str:
        return "●"

    def body(self) -> str:
        t = _theme.current()
        is_search = self.name == "web_search"
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
        return out


def _short(text: str, limit: int = 70) -> str:
    """Truncate text to *limit* chars, adding ellipsis if needed."""
    flat = " ".join(str(text).split())
    return flat[: limit - 1] + "…" if len(flat) > limit else flat


@dataclass
class ModeStatus(Block):
    """Permission mode notice: ``Mode: Auto`` with description."""

    mode_id: str = ""
    mode_name: str = ""
    description: str = ""
    kind: str = "notice"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        name = self.mode_name or self.mode_id
        line = f"{t.dim_style}Mode:{ansi.RESET} {t.tag_style}{name}{ansi.RESET}"
        if self.description:
            line += f" {t.dim_style}— {self.description}{ansi.RESET}"
        return [ansi.clip_ellipsis(line, width)]


@dataclass
class ThinkingLevelChanged(Block):
    """Thinking level change confirmation: ``Thinking: high``."""

    from_level: str = ""
    to_level: str = ""

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        line = f"{t.dim_style}Thinking{ansi.RESET}: {t.dim_style}{self.to_level}{ansi.RESET}"
        return [ansi.clip_ellipsis(line, width)]


@dataclass
class ModeChanged(Block):
    """Mode change confirmation: ``Switched to Ask — Confirm each action before it runs``."""

    from_mode: str = ""
    to_mode: str = ""
    to_name: str = ""
    description: str = ""
    kind: str = "notice"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        name = self.to_name or self.to_mode
        line = f"{t.dim_style}Switched to{ansi.RESET} {t.tag_style}{name}{ansi.RESET}"
        if self.description:
            line += f" {t.dim_style}— {self.description}{ansi.RESET}"
        return [ansi.clip_ellipsis(line, width)]


def spacing(previous: str | None, kind: str) -> int:
    """Blank rows fx leaves between two transcript blocks."""
    if previous is None:
        return 0
    if kind == "tool" and previous in ("tool", "assistant", "assistant_tool"):
        return 0
    if kind == "assistant" and previous in ("assistant", "assistant_tool"):
        return 0
    return 1
