"""Core transcript block primitives and conversation blocks."""

from __future__ import annotations

import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from functools import lru_cache

from vx.brand import ITALIC_BRAND
from vx.tui import ansi, cache, markdown
from vx.tui import theme as _theme

#: fx's assistant gutter: two columns of breathing room on every reply row.
GUTTER = 2
#: The connected rail that marks a user prompt in the transcript.
USER_RAIL = "┃"
#: Row budget for a `!cmd`'s folded output, mirroring fx's compact transcript.
OUTPUT_PREVIEW_ROWS = 5
#: Rows an option's preview may take while it is the banded choice.
ASK_PREVIEW_ROWS = 3

_NOTICE_GLYPHS = {
    "information": "i",
    "success": "✓",
    "warning": "!",
    "error": "✗",
    "cancelled": "⊘",
    "neutral": "*",
}


_expanded = False


def set_expanded(flag: bool) -> None:
    """ctrl+o unfolds a `!cmd`'s output, fx's full-transcript view."""
    global _expanded
    if flag == _expanded:
        return
    _expanded = flag
    # Every block's rows were built under the old expansion, so the cache has
    # to miss for all of them, not just for the ones that read ``_expanded``.
    cache.bump_generation()


def expanded() -> bool:
    return _expanded


def _gutter(rows: list[str], width: int) -> list[str]:
    lead = " " * GUTTER
    return [lead + r if r else "" for r in rows]


@dataclass
class Block(cache.Revisioned):
    kind: str = "notice"

    def rows(self, width: int) -> list[str]:
        raise NotImplementedError

    def parts(self) -> tuple[cache.Revisioned, ...]:
        """Nested values whose mutations change this block's rows."""
        return ()

    def cacheable(self) -> bool:
        """False for rows that follow a clock rather than the block's fields."""
        return True

    def signature(self) -> tuple:
        """This block's own revision, and its children's, for the row cache."""
        parts = self.parts()
        if not parts:
            return (self._rev,)
        return (self._rev, tuple(part.signature() for part in parts))


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


@lru_cache(maxsize=8192)
def _strip_thought(line: str) -> str:
    """ctx's collapsed-thinking flattening, memoized per finished line."""
    with suppress(Exception):
        # ctx flattens the markup in folded thinking, so vx shows the
        # same words it does instead of raw ``**emphasis**``.
        from ctx.term.formatting import strip_markdown_for_collapsed_text

        return strip_markdown_for_collapsed_text(line)
    return line


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
            out.extend(ansi.wrap_cached(_strip_thought(line), body_width))
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

        budget = sys.modules[__package__ or "vx.tui.blocks"].thinking_line_budget()
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
class AskChoice:
    """One offered answer: the label, a line saying what it means, a preview."""

    label: str = ""
    description: str = ""
    preview: str = ""


@dataclass
class AskItem(cache.Revisioned):
    """One question of an ``ask_user`` call with the answer it got (if any)."""

    question: str = ""
    options: list[AskChoice] = field(default_factory=list)
    answer: str | None = None
    custom: bool = False
    multi: bool = False
    checked: list[int] = field(default_factory=list)

    def signature(self) -> tuple:
        # ``checked`` is toggled in place as the reader picks answers, so the
        # revision alone would miss it; the values make the change visible.
        return (self._rev, tuple(self.checked), len(self.options))


@dataclass
class AskUser(Block):
    """The ``ask_user`` tool: questions the model cannot answer by itself.

    It is a tool row like any other, so the question stays welded to the turn
    that asked it instead of floating over the composer. A call may carry
    several questions: answered ones fold to ``✓ question → answer``, the open
    one shows its choices under the selection band, and the ones still to come
    wait as dim lines — the whole request is visible before it is answered.
    """

    items: list[AskItem] = field(default_factory=list)
    at: int = 0
    selected: int = 0
    draft: str = ""
    skipped: bool = False
    started: float | None = None
    ended: float | None = None
    kind: str = "tool"

    @property
    def waiting(self) -> bool:
        return not self.skipped and self.at < len(self.items)

    def parts(self) -> tuple[cache.Revisioned, ...]:
        return tuple(self.items)

    def cacheable(self) -> bool:
        # Finished or still waiting draws a fixed clock; a cancelled call that
        # never reported an end keeps counting, so it must not be frozen.
        return self.waiting or self.ended is not None or self.started is None

    def current(self) -> AskItem | None:
        return self.items[self.at] if 0 <= self.at < len(self.items) else None

    def elapsed(self) -> float | None:
        if self.started is None:
            return None
        return (self.ended if self.ended is not None else time.monotonic()) - self.started

    def rows(self, width: int) -> list[str]:
        t = _theme.current()
        total = len(self.items)
        asked = f"Asked you {total} questions" if total > 1 else "Asked you a question"
        head = f"{ansi.BOLD}{asked}{ansi.RESET}"
        progress = (
            f"{t.dim_style} · {min(self.at + 1, total)}/{total}{ansi.RESET}" if total > 1 else ""
        )
        seconds = self.elapsed()
        clock = f" · {_elapsed_label(seconds)}" if seconds is not None and not self.waiting else ""
        if self.skipped:
            tail = f"{clock} {t.red_style}✗{ansi.RESET}"
        elif self.waiting:
            tail = f"{t.dim_style} · waiting{ansi.RESET}{progress}"
        else:
            tail = f"{clock} {t.green_style}✓{ansi.RESET}"
        glyph = f"{t.dim_style}●{ansi.RESET}" if self.waiting else "●"
        out = [ansi.clip(f"{glyph} {head}{tail}", width)]
        for item in self.items[: self.at]:
            out.extend(self._answered_rows(item, width))
        current = self.current()
        if current is not None and not self.skipped:
            for line in ansi.wrap(" ".join(current.question.split()), max(8, width - 4)):
                out.append(f"{t.dim_style}├{ansi.RESET} {t.hint_style}{line}{ansi.RESET}")
            # One digit, two spaces, the longest label, and a cell of slack.
            longest = max((len(choice.label) for choice in current.options), default=8)
            band = min(longest + 4, max(8, width - 6))
            detail_room = max(0, width - band - 8)
            for index, choice in enumerate(current.options):
                detail = ansi.clip_ellipsis(choice.description, detail_room)
                tail_rows = f"  {t.dim_style}{detail}{ansi.RESET}" if detail else ""
                mark = ""
                if current.multi:
                    mark = "[x] " if index in current.checked else "[ ] "
                text = f"{index + 1}  {mark}{choice.label}"
                if index == self.selected:
                    cell = ansi.pad(text, band + len(mark))
                    out.append(
                        f"{t.dim_style}├{ansi.RESET} {t.selected_completion_style}"
                        f"{cell}{ansi.RESET}{tail_rows}"
                    )
                else:
                    # Padded like the banded row, so every description starts
                    # in the same column.
                    label = ansi.clip_ellipsis(choice.label, max(4, band - 3))
                    cell = ansi.pad(f"{index + 1}  {mark}{label}", band + len(mark))
                    out.append(
                        f"{t.dim_style}├{ansi.RESET} {t.dim_style}{cell}{ansi.RESET}{tail_rows}"
                    )
                # Only the banded choice spends rows on its preview.
                if index == self.selected and choice.preview:
                    out.extend(self._preview_rows(choice.preview, width))
        # Everything after the open question is still to come.
        for item in self.items[self.at + 1 :] if not self.skipped else []:
            pending = ansi.clip_ellipsis(item.question, max(8, width - 6))
            out.append(f"{t.dim_style}├ · {pending}{ansi.RESET}")
        if self.waiting:
            draft = " ".join(self.draft.split())
            if draft:
                typed = ansi.clip_ellipsis(draft, max(4, width - 8))
                out.append(
                    f"{t.dim_style}├{ansi.RESET} {t.selected_completion_style}{typed}{ansi.RESET}"
                )
            count = len(current.options) if current is not None else 0
            if current is not None and current.multi and count:
                hint = f"1-{count} toggles · enter confirms · esc skips"
            elif not count:
                hint = "enter sends your text · esc skips"
            else:
                hint = _ask_hint(count)
            out.append(f"{t.dim_style}└ {ansi.clip_ellipsis(hint, max(4, width - 2))}{ansi.RESET}")
            return [ansi.clip(row, width) if row else "" for row in out]
        if self.skipped:
            answered = sum(1 for item in self.items if item.answer)
            left = total - answered
            body = (
                "skipped" if not answered else f"{answered} of {total} answered · skipped {left}"
            )
            out.append(f"{t.dim_style}└ {body}{ansi.RESET}")
        elif total > 1:
            out.append(f"{t.dim_style}└ all {total} answered{ansi.RESET}")
        return [ansi.clip(row, width) if row else "" for row in out]

    def _preview_rows(self, preview: str, width: int) -> list[str]:
        """A selected option's markdown, a few dim lines, never unbounded."""
        t = _theme.current()
        body = [line for line in preview.strip().splitlines() if line.strip()]
        rows: list[str] = []
        for line in body:
            for wrapped in ansi.wrap(line.rstrip(), max(8, width - 6)):
                rows.append(f"{t.dim_style}│   {wrapped}{ansi.RESET}")
                if len(rows) >= ASK_PREVIEW_ROWS:
                    return rows
        return rows

    def _answered_rows(self, item: AskItem, width: int) -> list[str]:
        t = _theme.current()
        if not item.answer:
            skipped = ansi.clip_ellipsis(item.question, max(8, width - 6))
            return [f"{t.dim_style}├ ✗ {skipped}{ansi.RESET}"]
        # Only a ticked multi-select question reports its ticks: answering by
        # typing on the same question is a plain answer.
        if item.multi and item.checked:
            names = ", ".join(
                item.options[index].label for index in item.checked if index < len(item.options)
            )
            head = ansi.clip_ellipsis(item.question, max(8, width // 2))
            tail = ansi.clip_ellipsis(names or "none", max(8, width - ansi.width(head) - 8))
            line = (
                f"{t.dim_style}├{ansi.RESET} {t.green_style}✓{ansi.RESET} "
                f"{t.dim_style}{head}{ansi.RESET}  {t.dim_style}→{ansi.RESET}  "
                f"{t.hint_style}{tail}{ansi.RESET}"
            )
            return [ansi.clip(line, width)]
        room = max(10, width - 6)
        head = ansi.clip_ellipsis(item.question, max(8, room // 2))
        tail = ansi.clip_ellipsis(item.answer, max(8, room - ansi.width(head) - 4))
        line = (
            f"{t.dim_style}├{ansi.RESET} {t.green_style}✓{ansi.RESET} "
            f"{t.dim_style}{head}{ansi.RESET}  {t.dim_style}→{ansi.RESET}  "
            f"{t.hint_style}{tail}{ansi.RESET}"
        )
        return [ansi.clip(line, width)]


def _ask_hint(count: int) -> str:
    return f"1-{count} or ↑↓ · type an answer · enter sends · esc skips"


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
