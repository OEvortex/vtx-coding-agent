"""The vx footer: composer, hint row, pickers, approval and question panels.

Every shape here mirrors fx: the prompt glyph on the first composer row,
two-space continuations, rules above and below, and a statusline hint row
with middle-dot separators plus a right-aligned activity note.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from vx.tui import ansi
from vx.tui import theme as _theme

PROMPT = "❯ "
CONTINUATION = "  "
SEPARATOR = " · "

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def rule(cols: int) -> str:
    t = _theme.current()
    return f"{t.divider_style}{'─' * max(1, cols)}{ansi.RESET}"


# -- composer ----------------------------------------------------------------


def wrap_input(text: str, cols: int) -> list[tuple[int, int]]:
    """Split input into visual rows; each row is a (start, end) char range."""
    usable = max(1, cols - 2)
    rows: list[tuple[int, int]] = []
    start = 0
    used = 0
    i = 0
    for ch in text:
        if ch == "\n":
            rows.append((start, i))
            i += 1
            start = i
            used = 0
            continue
        cw = ansi.char_width(ch)
        if cw and used + cw > usable:
            rows.append((start, i))
            start = i
            used = 0
        used += cw
        i += 1
    rows.append((start, i))
    return rows


def cursor_visual(text: str, cursor: int, cols: int) -> tuple[int, int]:
    """(visual row index, terminal column 0-based) of the caret."""
    rows = wrap_input(text, cols)
    for index, (start, end) in enumerate(rows):
        if cursor <= end:
            prefix = text[start:cursor]
            return index, 2 + ansi.width(prefix)
    last_start = rows[-1][0]
    return len(rows) - 1, 2 + ansi.width(text[last_start:cursor])


def composer_rows(text: str, cursor: int, cols: int, max_rows: int) -> tuple[list[str], int, int]:
    """Styled composer rows plus the caret's (row, column) inside them."""
    rows = wrap_input(text, cols)
    cur_row, cur_col = cursor_visual(text, cursor, cols)
    visible = max(1, max_rows)
    first = 0
    if len(rows) > visible:
        first = max(0, min(cur_row - visible + 1, len(rows) - visible))
    window = rows[first : first + visible]
    out: list[str] = []
    for index, (start, end) in enumerate(window):
        prefix = PROMPT if first + index == 0 else CONTINUATION
        out.append(prefix + text[start:end])
    while len(out) < visible:
        out.append("")
    return out, cur_row - first, cur_col


# -- hint row ----------------------------------------------------------------


def compact_model(model: str) -> str:
    bare = model.rsplit("/", 1)[-1]
    for old, new in (
        ("claude-opus-", "opus "),
        ("claude-sonnet-", "sonnet "),
        ("claude-haiku-", "haiku "),
    ):
        if bare.startswith(old):
            return new + bare[len(old) :]
    return bare


def permission_label(mode: str) -> str:
    t = _theme.current()
    if mode in ("auto", "full-access", "yolo"):
        text = "full access" if mode != "auto" else "auto"
        return f"{t.permission_auto_style}{text}{t.statusline_style}"
    return mode


def hint_row(
    *,
    cols: int,
    permission: str,
    model: str,
    effort: str,
    context_used: int,
    context_total: int | None,
    workspace: str,
    branch: str | None = None,
    right: str = "",
) -> str:
    t = _theme.current()
    segments: list[str] = [permission_label(permission)]
    if model:
        segments.append(compact_model(model))
    if effort and effort != "auto":
        segments.append(effort)
    if context_used > 0:
        if context_total:
            segments.append(
                f"{context_used // 1000}k/{context_total // 1000}k "
                f"{context_used * 100 // max(1, context_total)}%"
            )
        else:
            segments.append(f"{context_used // 1000}k")
    identity = _workspace_identity(workspace, branch, cols)
    left = SEPARATOR.join(segments)
    right_width = ansi.width(right)
    left_budget = cols - right_width - 1 if right and cols > right_width else cols
    if identity:
        room = left_budget - ansi.width(left) - len(SEPARATOR)
        if room >= 4:
            identity = _fit_identity(workspace, branch, room)
            left = f"{left}{SEPARATOR}{identity}"
    left_text = ansi.clip_ellipsis(left, max(0, left_budget))
    row = f"{t.statusline_style}{left_text}{ansi.RESET}"
    if right:
        pad = cols - ansi.width(left_text) - right_width
        if pad > 0:
            row += " " * pad + f"{t.dim_style}{right}{ansi.RESET}"
        else:
            row = f"{t.statusline_style}{ansi.clip_ellipsis(left, cols)}{ansi.RESET}"
    return ansi.pad(row, cols) if ansi.width(row) < cols else ansi.clip(row, cols)


def _fit_identity(workspace: str, branch: str | None, room: int) -> str:
    """fx keeps the tail of a long path and the branch beside it."""
    path = _short_home(workspace)
    tail = f" ({branch})" if branch else ""
    if ansi.width(path) + ansi.width(tail) <= room:
        return path + tail
    keep = room - 1 - ansi.width(tail)
    if keep >= 3:
        return "…" + ansi._suffix_plain(path, keep) + tail
    return "…" + ansi._suffix_plain(path, max(1, room - 1))


def _short_home(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home) :] if path.startswith(home) else path


def _workspace_identity(workspace: str, branch: str | None, cols: int) -> str:
    if not workspace:
        return ""
    name = workspace
    home = os.path.expanduser("~")
    if name.startswith(home):
        name = "~" + name[len(home) :]
    if branch:
        return f"{name} ({branch})"
    return name


def activity_note(label: str, started: float, now: float, queued: int = 0) -> str:
    frame = SPINNER[int((now - started) * 10) % len(SPINNER)]
    note = f"{frame} {label}"
    if queued:
        note += f" · {queued} queued"
    return note


# -- pickers (slash menu, model menu, help) ----------------------------------


@dataclass
class MenuItem:
    label: str
    detail: str = ""
    annotation: str = ""


def menu_rows(
    *, cols: int, items: list[MenuItem], selected: int, header: str = "", window: int = 10
) -> list[str]:
    """fx picker rows: the selection glyph, a label, then a detail column."""
    t = _theme.current()
    rows: list[str] = []
    if header:
        rows.append(f"{t.dim_style}{ansi.clip_ellipsis(header, cols)}{ansi.RESET}")
    if not items:
        return rows
    selected = max(0, min(selected, len(items) - 1))
    start = 0
    if len(items) > window:
        # fx keeps the selection near the middle of the visible window.
        start = max(0, min(selected - window // 2, len(items) - window))
    shown = items[start : start + window]

    label_width = max((ansi.width(i.label) for i in items), default=0)
    detail_col = 0
    if any(i.detail for i in items):
        detail_col = min(cols, max(label_width + 4, 28))
    for index, item in enumerate(shown):
        is_selected = start + index == selected
        style = t.selected_completion_style if is_selected else t.dim_style
        marker = "› " if is_selected else "  "
        label = ansi.clip_ellipsis(item.label, max(1, (detail_col or cols) - 2))
        row = f"{style}{marker}{label}{ansi.RESET}"
        if item.detail and detail_col:
            row = ansi.pad(row, detail_col)
            row += f"{t.dim_style}{ansi.clip_ellipsis(item.detail, cols - detail_col)}{ansi.RESET}"
        elif item.annotation:
            row = ansi.pad(row, max(0, cols - ansi.width(item.annotation) - 3))
            row += f"{t.dim_style}{item.annotation}{ansi.RESET}"
        rows.append(ansi.clip(row, cols))
    if len(items) > window:
        span = f"{start + 1}–{start + len(shown)}/{len(items)}"
        if header:
            left = ansi.clip_ellipsis(header, max(1, cols - ansi.width(span) - 2))
            rows[0] = (
                ansi.pad(f"{t.dim_style}{left}{ansi.RESET}", cols - ansi.width(span))
                + f"{t.dim_style}{span}{ansi.RESET}"
            )
        else:
            rows[0] = rows[0] + f"{t.dim_style}{span}{ansi.RESET}"
    return rows


# -- approval panel ----------------------------------------------------------


@dataclass
class ApprovalView:
    label: str = ""
    target: str = ""
    reason: str = ""
    choices: list[str] = field(default_factory=list)
    selected: int = 0

    def panel(self, cols: int) -> list[str]:
        t = _theme.current()
        rows = ["  Permission needed · Choose one"]
        rows.append(f"  {ansi.BOLD}Would you like to allow this action?{ansi.DIM_OFF}")
        if self.reason:
            rows.append(
                f"  {t.dim_style}Reason:{ansi.RESET} {ansi.clip_ellipsis(self.reason, cols - 11)}"
            )
        if self.target:
            rows.append(f"  {ansi.clip_ellipsis(self.target, cols - 2)}")
        for index, choice in enumerate(self.choices):
            marker = "❯ " if index == self.selected else "  "
            if index == self.selected:
                rows.append(f"  {marker}{t.tag_style}{choice}{ansi.RESET}")
            else:
                rows.append(f"  {marker}{choice}")
        return [ansi.clip(r, cols) for r in rows]

    def hint(self, cols: int) -> str:
        t = _theme.current()
        variants = (
            "1–3 choose now    ↑↓ or tab options    enter confirm    esc cancel",
            "1–3 choose now    enter confirm    esc cancel",
            "enter confirm    esc cancel",
        )
        for candidate in variants:
            if ansi.width(candidate) <= cols:
                return f"{t.dim_style}{candidate}{ansi.RESET}"
        return f"{t.dim_style}{variants[-1]}{ansi.RESET}"


# -- question panel ----------------------------------------------------------


@dataclass
class QuestionOption:
    label: str
    description: str = ""


@dataclass
class QuestionView:
    question: str = ""
    options: list[QuestionOption] = field(default_factory=list)
    selected: int = 0
    index: int = 0
    total: int = 1
    draft: str = ""
    draft_cursor: int = 0

    def panel(self, cols: int) -> list[str]:
        t = _theme.current()
        rows: list[str] = []
        question = self.question.strip() or "Question"
        for line in ansi.wrap(question, max(1, cols - 2)):
            rows.append(f"  {ansi.BOLD}{line}{ansi.DIM_OFF}")
        rows.append("")
        for index, option in enumerate(self.options):
            selected = index == self.selected
            style = t.selected_completion_style if selected else t.dim_style
            label = f"{index + 1}) {option.label}"
            row = f"    {style}{label}{ansi.RESET}"
            if option.description:
                desc_col = min(cols, max(ansi.width(label) + 8, cols // 2))
                row = ansi.pad(row, desc_col)
                detail = ansi.clip_ellipsis(option.description, max(1, cols - desc_col))
                row += f"{t.dim_style}{detail}{ansi.RESET}"
            rows.append(ansi.clip(row, cols))
        if self.draft:
            style = (
                t.selected_completion_style
                if self.selected >= len(self.options)
                else t.system_notice_text_style
            )
            draft = ansi.clip_ellipsis(self.draft, max(1, cols - 6))
            rows.append(f"    {style}{draft}{ansi.RESET}")
        rows.append("")
        return [ansi.clip(r, cols) if r else "" for r in rows]

    def hint(self, cols: int) -> str:
        t = _theme.current()
        progress = f"{self.index + 1}/{self.total} · " if self.total > 1 else ""
        variants = (
            f"{progress}↑↓ options    type to answer    enter confirm    esc skip",
            f"{progress}enter confirm    esc skip",
        )
        for candidate in variants:
            if ansi.width(candidate) <= cols:
                return f"{t.dim_style}{candidate}{ansi.RESET}"
        return f"{t.dim_style}{variants[-1]}{ansi.RESET}"
