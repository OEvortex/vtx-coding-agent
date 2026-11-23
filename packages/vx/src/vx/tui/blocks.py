"""Transcript blocks, drawn the way fx draws them.

Each block turns itself into styled rows at a given width. The viewport adds
the blank rows between blocks and pins the welcome banner, so blocks never
worry about neighbours.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

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
ToolStatusBlock: TypeAlias = "ToolStatus | ReadToolStatus | BashToolStatus | ApplyPatchToolStatus | GrepToolStatus | WebToolStatus"


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
class ToolStatus(Block):
    """``● Ran zig build ✓``: one row per call, fx's transcript activity."""

    name: str = ""
    target: str = ""
    status: str = "running"  # running | ok | error
    calls: int = 1
    kind: str = "tool"

    def _verb(self) -> str:
        return _TOOL_VERBS.get(self.name, self.name)

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        verb = self._verb()
        target = self.target
        if self.status == "running":
            line = f"{t.dim_style}●{ansi.RESET} {t.dim_style}Running{ansi.RESET}"
            if target:
                line += f" {target}"
        else:
            line = f"{t.dim_style}●{ansi.RESET} {verb}"
            if target:
                line += f" {t.tag_style}{target}{ansi.RESET}"
        if self.calls > 1 and self.status != "running":
            line += f"{t.dim_style} · {self.calls} calls{ansi.RESET}"
        if self.status == "ok":
            line += f" {t.task_completed_open}✓{ansi.RESET}"
        elif self.status == "error":
            line += f"{t.dim_style} ✗{ansi.RESET}"
        return [ansi.clip_ellipsis(line, width)]


@dataclass
class ToolGroup(Block):
    """A compact group of tool calls, with one indented row per call."""

    calls: list[ToolStatusBlock] = field(default_factory=list)
    status: str = "running"
    kind: str = "tool"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        n = len(self.calls)
        failed = sum(call.status == "error" for call in self.calls)
        names = {call.name for call in self.calls}
        verb = self.calls[0]._verb() if len(names) == 1 and self.calls else "Tools"
        noun = "tool" if n == 1 else "tools"
        if self.status == "running":
            heading = f"{t.dim_style}● Running {n} {noun}{ansi.RESET}"
        else:
            heading = f"{t.dim_style}● {verb} {n} {noun}{ansi.RESET}"
            if failed:
                heading += f" {t.dim_style}({failed} failed){ansi.RESET}"
            else:
                heading += f" {t.task_completed_open}✓{ansi.RESET}"
        out = [ansi.clip_ellipsis(heading, width)]
        for call in self.calls:
            marker = f"{t.dim_style}│{ansi.RESET}"
            rows = call.rows(max(1, width - 4)) or [getattr(call, "target", "") or call._verb()]
            for i, row in enumerate(rows):
                prefix = f"  {marker} " if i == 0 else "    "
                out.append(ansi.clip_ellipsis(prefix + row, width))
        return out


@dataclass
class ToolOutput(Block):
    """Folded command output: dim ``│ `` rows under the status line."""

    lines: list[str] = field(default_factory=list)
    buffer: str = ""
    hidden: int = 0
    kind: str = "tool"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        rail = f"{t.tool_stdout_style}│ {ansi.RESET}"
        out: list[str] = []
        limit = OUTPUT_EXPANDED_ROWS if _expanded else OUTPUT_PREVIEW_ROWS
        shown = self.lines[:limit]
        for line in shown:
            chunk = ansi.clip(line.rstrip(), max(1, width - 2))
            out.append(f"{rail}{t.tool_stdout_style}{chunk.rstrip()}{ansi.RESET}")
        hidden = self.hidden + max(0, len(self.lines) - limit)
        if hidden > 0:
            hint = "ctrl+o to collapse" if _expanded else "ctrl+o to view"
            out.append(f"{rail}{t.dim_style}… {hidden} more lines ({hint}){ansi.RESET}")
        return out


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
class ReadToolStatus(Block):
    """Read tool: `F path:12-30` with line count."""

    name: str = "read"
    path: str = ""
    start_line: int = 0
    end_line: int = 0
    total_lines: int = 0
    is_dir: bool = False
    status: str = "running"
    kind: str = "tool"

    def _verb(self) -> str:
        return _TOOL_VERBS.get(self.name, self.name)

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        if self.status == "running":
            line = f"{t.dim_style}●{ansi.RESET} {t.dim_style}Reading{ansi.RESET}"
            if self.path:
                line += f" {t.tag_style}{self.path}{ansi.RESET}"
        else:
            glyph = "F" if not self.is_dir else "D"
            line = f"{t.dim_style}{glyph}{ansi.RESET} {t.tag_style}{self.path}{ansi.RESET}"
            if self.start_line and self.end_line:
                if self.start_line == self.end_line:
                    line += f" {t.dim_style}:L{self.start_line}{ansi.RESET}"
                else:
                    line += f" {t.dim_style}:L{self.start_line}-{self.end_line}{ansi.RESET}"
            if self.total_lines and not self.is_dir:
                line += f"{t.dim_style} · {self.total_lines}L{ansi.RESET}"
            if self.status == "ok":
                line += f" {t.task_completed_open}✓{ansi.RESET}"
            elif self.status == "error":
                line += f"{t.dim_style} ✗{ansi.RESET}"
        return [ansi.clip_ellipsis(line, width)]


@dataclass
class BashToolStatus(Block):
    """Bash tool: ``● Ran command`` — fx's compact one-row status."""

    name: str = "bash"
    command: str = ""
    exit_code: int | None = None
    status: str = "running"
    kind: str = "tool"

    def _verb(self) -> str:
        return _TOOL_VERBS.get(self.name, self.name)

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        if self.status == "running":
            line = f"{t.dim_style}●{ansi.RESET} {t.dim_style}Running{ansi.RESET}"
            if self.command:
                line += f" {t.tool_stdout_style}{self.command}{ansi.RESET}"
        else:
            verb = self._verb()
            line = f"{t.dim_style}●{ansi.RESET} {verb}"
            if self.command:
                line += f" {t.tool_stdout_style}{self.command}{ansi.RESET}"
            if self.exit_code is not None and self.exit_code != 0:
                line += f"{t.dim_style} (exit {self.exit_code}){ansi.RESET}"
            if self.status == "ok":
                line += f" {t.task_completed_open}✓{ansi.RESET}"
            elif self.status == "error":
                line += f"{t.dim_style} ✗{ansi.RESET}"
        return [ansi.clip_ellipsis(line, width)]


@dataclass
class DiffOutput(Block):
    """fx-style diff preview: ``  │ N + text`` with colored markers."""

    lines: list[tuple[str, str, str]] = field(default_factory=list)
    """List of (op, line_number, text) where op is '+', '-', or ' '."""
    kind: str = "tool_output"

    def rows(self, width: int) -> list[str]:
        if not self.lines:
            return [""]
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
class ApplyPatchToolStatus(Block):
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

    def _verb(self) -> str:
        return _TOOL_VERBS.get(self.name, self.name)

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        if self.status == "running":
            line = f"{t.dim_style}●{ansi.RESET} {t.dim_style}Editing{ansi.RESET}"
            if self.path:
                line += f" {t.tool_stdout_style}{self.path}{ansi.RESET}"
        else:
            verb = self._verb()
            line = f"{t.dim_style}●{ansi.RESET} {verb}"
            if self.path:
                line += f" {t.tool_stdout_style}{self.path}{ansi.RESET}"
            # fx-style diff stats: +N / -M in dim
            if self.added > 0 and self.removed > 0:
                line += f" {t.dim_style}+{self.added}{ansi.RESET}"
                line += f" {t.dim_style}/{ansi.RESET}"
                line += f" {t.dim_style}-{self.removed}{ansi.RESET}"
            elif self.added > 0:
                line += f" {t.dim_style}+{self.added}{ansi.RESET}"
            elif self.removed > 0:
                line += f" {t.dim_style}-{self.removed}{ansi.RESET}"
            if self.status == "error":
                line += f"{t.dim_style} ✗{ansi.RESET}"
        return [ansi.clip_ellipsis(line, width)]


@dataclass
class GrepToolStatus(Block):
    """grep tool: `? pattern in path` with match count."""

    name: str = "grep"
    pattern: str = ""
    path: str = ""
    match_count: int = 0
    status: str = "running"
    kind: str = "tool"

    def _verb(self) -> str:
        return _TOOL_VERBS.get(self.name, self.name)

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        if self.status == "running":
            line = f"{t.dim_style}●{ansi.RESET} {t.dim_style}Searching{ansi.RESET}"
            if self.pattern:
                line += f" {t.tag_style}{self.pattern}{ansi.RESET}"
        else:
            line = f"{t.dim_style}?{ansi.RESET} {t.tag_style}{self.pattern}{ansi.RESET}"
            if self.path:
                line += f" {t.dim_style}in{ansi.RESET} {t.tag_style}{self.path}{ansi.RESET}"
            if self.match_count:
                noun = "match" if self.match_count == 1 else "matches"
                line += f" {t.dim_style}· {self.match_count} {noun}{ansi.RESET}"
            if self.status == "ok":
                line += f" {t.task_completed_open}✓{ansi.RESET}"
            elif self.status == "error":
                line += f"{t.dim_style} ✗{ansi.RESET}"
        return [ansi.clip_ellipsis(line, width)]


@dataclass
class WebToolStatus(Block):
    """web/web_search tool: `W query` or `L url`."""

    name: str = "web"
    query: str = ""
    url: str = ""
    result_count: int = 0
    status: str = "running"
    kind: str = "tool"

    def _verb(self) -> str:
        return _TOOL_VERBS.get(self.name, self.name)

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        is_search = self.name == "web_search"
        if self.status == "running":
            verb = "Searching" if is_search else "Fetching"
            line = f"{t.dim_style}●{ansi.RESET} {t.dim_style}{verb}{ansi.RESET}"
            target = self.query or self.url
            if target:
                line += f" {t.tag_style}{_short(target, 50)}{ansi.RESET}"
        else:
            line = f"{t.dim_style}W{ansi.RESET}"
            target = self.query or self.url
            if target:
                line += f" {t.tag_style}{_short(target, 50)}{ansi.RESET}"
            if self.result_count:
                noun = "result" if self.result_count == 1 else "results"
                line += f" {t.dim_style}· {self.result_count} {noun}{ansi.RESET}"
            if self.status == "ok":
                line += f" {t.task_completed_open}✓{ansi.RESET}"
            elif self.status == "error":
                line += f"{t.dim_style} ✗{ansi.RESET}"
        return [ansi.clip_ellipsis(line, width)]


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
