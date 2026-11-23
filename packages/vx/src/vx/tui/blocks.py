"""Transcript blocks, drawn the way fx draws them.

Each block turns itself into styled rows at a given width. The viewport adds
the blank rows between blocks and pins the welcome banner, so blocks never
worry about neighbours.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    "todo": "Planned",
    "ask_user": "Asked",
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


@dataclass
class Welcome(Block):
    """fx's welcome line: bright brand, dim build label and hint."""

    version: str = ""
    kind: str = "welcome"

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        brand = "vx"
        label = f"v{self.version}" if self.version else ""
        rows = ansi.wrap(
            f"{t.subtitle_style}{brand}{ansi.RESET}"
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


def spacing(previous: str | None, kind: str) -> int:
    """Blank rows fx leaves between two transcript blocks."""
    if previous is None:
        return 0
    if kind == "tool" and previous in ("tool", "assistant", "assistant_tool"):
        return 0
    if kind == "assistant" and previous in ("assistant", "assistant_tool"):
        return 0
    return 1
