"""The vx footer: composer, hint row, pickers, approval and question panels.

Every shape here mirrors fx: the prompt glyph on the first composer row,
two-space continuations, rules above and below, and a statusline hint row
with middle-dot separators plus a right-aligned activity note.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

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


def composer_window(text: str, cursor: int, cols: int, max_rows: int) -> tuple[int, int, int]:
    """``(total rows, caret row, first row that keeps the caret visible)``.

    The composer can hold more lines than it shows, so paging inside it needs
    the same numbers the drawing code uses.
    """
    rows = wrap_input(text, cols)
    caret_row, _ = cursor_visual(text, cursor, cols)
    visible = max(1, max_rows)
    total = len(rows)
    if total <= visible:
        return total, caret_row, 0
    return total, caret_row, max(0, min(caret_row - visible + 1, total - visible))


def composer_rows(
    text: str,
    cursor: int,
    cols: int,
    max_rows: int,
    *,
    secret: bool = False,
    scroll: int | None = None,
) -> tuple[list[str], int, int]:
    """Styled composer rows plus the caret's (row, column) inside them.

    ``scroll`` pins the first visible row; ``None`` follows the caret. When the
    pinned window does not contain the caret its row comes back negative, which
    the caller reads as "hide the cursor".
    """
    if secret:
        # A pasted API key must not echo; the caret still sits after the mask.
        text = "•" * len(text)
    visible = max(1, max_rows)
    total, cur_row, follow = composer_window(text, cursor, cols, max_rows)
    _, cur_col = cursor_visual(text, cursor, cols)
    first = follow
    if scroll is not None and total > visible:
        first = max(0, min(scroll, total - visible))
    rows = wrap_input(text, cols)
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
    if mode in ("auto",):
        return f"{t.permission_auto_style}auto{t.statusline_style}"
    if mode in ("ask", "prompt"):
        return f"{t.permission_auto_style}ask{t.statusline_style}"
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
    segments: list[str] = []
    if permission:
        segments.append(permission_label(permission))
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
            left = f"{left}{SEPARATOR}{identity}" if left else identity
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


def elapsed_note(seconds: float) -> str:
    """``7s``, ``1m 20s``, ``2h 5m`` -- short enough for the status row."""
    total = round(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60}s"
    return f"{total // 3600}h {(total % 3600) // 60}m"


def activity_note(label: str, started: float, now: float, queued: int = 0) -> str:
    """The running note: spinner, what is happening, how long, and the way out."""
    elapsed = max(0.0, now - started)
    frame = SPINNER[int(elapsed * 10) % len(SPINNER)]
    note = f"{frame} {label}"
    if elapsed >= 1:
        note += f" {elapsed_note(elapsed)}"
    note += " · esc to interrupt"
    if queued:
        note += f" · {queued} queued"
    return note


def attachment_row(attachments: Sequence[Any], cols: int) -> str:
    """The chips above the composer: what the next prompt will carry.

    fx shows an attachment as a dim chip with its size, plus how to drop it, so
    a pasted screenshot is visible before it is sent.
    """
    t = _theme.current()
    chips: list[str] = []
    for item in attachments:
        label = str(getattr(item, "label", "") or "image")
        size = str(getattr(item, "size_label", "") or "")
        chips.append(f"{label} {size}".strip())
    text = "  ".join(f"⧉ {chip}" for chip in chips)
    hint = "ctrl+x to drop" if len(chips) == 1 else "ctrl+x drops the last"
    body = f"{text}   {ansi.DIM_OFF}{hint}" if text else hint
    return ansi.clip_ellipsis(f"  {t.tag_style}{body}{ansi.RESET}", cols)


# -- pickers (slash menu, model menu, help) ----------------------------------


@dataclass
class MenuItem:
    """One picker row: label, description, and the metadata column label."""

    label: str
    detail: str = ""
    metadata: str = ""
    #: What a picker callback sees; defaults to the label when unset.
    value: str = ""


#: fx shows at most six inline picker rows (``list_window.default_max_picker_rows``).
MENU_MAX_ROWS = 6
MENU_LABEL_GAP = 3
MENU_MIN_DESCRIPTION = 24


def menu_window(count: int, selected: int, max_rows: int) -> tuple[int, int]:
    """(start, end) of the visible window, scrolled in fx's edge style."""
    rows = max(1, min(max_rows, count))
    start = max(0, min(selected - rows + 1, count - rows))
    return start, start + rows


def menu_header(
    *, noun: str, count: int, start: int, end: int, cols: int, type_hint: bool = False
) -> str:
    """fx's ``Results N`` header with the window range pushed right."""
    t = _theme.current()
    content_width = max(1, cols - 1)
    left = f"{noun} {count}"
    if type_hint:
        left += " · type to filter"
    right = f"{start + 1}\u2013{end}" if end - start < count else ""
    right_width = ansi.width(right)
    if right_width and content_width > right_width + 2:
        left_budget = content_width - right_width - 2
        row = ansi.pad(
            f"{t.dim_style}{ansi.clip_ellipsis(left, left_budget)}{ansi.RESET}",
            content_width - right_width,
        )
        return row + f"{t.dim_style}{right}{ansi.RESET}"
    return f"{t.dim_style}{ansi.clip_ellipsis(left, content_width)}{ansi.RESET}"


def menu_rows(
    *,
    cols: int,
    items: list[MenuItem],
    selected: int,
    noun: str = "Commands",
    type_hint: bool = False,
    max_rows: int = MENU_MAX_ROWS,
) -> list[str]:
    """fx's three-column picker: label, description, metadata at the right edge.

    The selection is brightness only (no caret), the label column is as wide as
    its widest row plus three, and the metadata column is left-aligned at
    ``width - 1 - metadata_width`` so every row lines up.
    """
    t = _theme.current()
    if not items or cols <= 0:
        return []
    selected = max(0, min(selected, len(items) - 1))
    start, end = menu_window(len(items), selected, max_rows)
    shown = items[start:end]

    content_width = max(1, cols - 1)
    indent = 2
    label_col = max((ansi.width(item.label) for item in shown), default=0)
    metadata_col = max((ansi.width(item.metadata) for item in shown), default=0)
    description_start = min(content_width, indent + label_col + MENU_LABEL_GAP)
    metadata_start = content_width - metadata_col

    rows = [
        menu_header(
            noun=noun, count=len(items), start=start, end=end, cols=cols, type_hint=type_hint
        ),
        "",
    ]
    for index, item in enumerate(shown):
        is_selected = start + index == selected
        style = t.selected_completion_style if is_selected else t.dim_style
        row = f"{style}{' ' * indent}"
        row += ansi.clip_ellipsis(item.label, max(1, content_width - indent))
        label_width = ansi.width(item.label)
        gap = max(description_start, indent + max(label_col, label_width) + MENU_LABEL_GAP)

        metadata_fits = metadata_col > 0 and item.metadata != "" and metadata_col <= content_width
        show_metadata = metadata_fits and (
            metadata_start >= gap + MENU_MIN_DESCRIPTION + MENU_LABEL_GAP
            if item.detail
            else metadata_start >= gap + MENU_LABEL_GAP
        )
        description_width = (
            metadata_start - gap - MENU_LABEL_GAP if show_metadata else content_width - gap
        )
        row = ansi.pad(row, min(content_width, description_start))
        if item.detail and description_width > 0:
            row += f"{t.dim_style}{ansi.clip_ellipsis(item.detail, description_width)}{ansi.RESET}"
        if show_metadata:
            row = ansi.pad(row, metadata_start)
            row += f"{t.dim_style}{item.metadata}{ansi.RESET}"
        rows.append(row)
    return [ansi.clip(row, cols) if row else "" for row in rows]


PROMPT_HINT_VARIANTS: tuple[str, ...] = (
    "enter submit     esc cancel",
    "enter submit  esc cancel",
    "enter  esc",
)


def prompt_row(label: str, cols: int) -> str:
    """The question above a one-line text prompt, as fx prints it."""
    t = _theme.current()
    return f"  {t.dim_style}{ansi.clip_ellipsis(label, max(1, cols - 2))}{ansi.RESET}"


MENU_HINT_VARIANTS: tuple[str, ...] = (
    "\u2191\u2193 navigate     enter use     esc close",
    "\u2191\u2193 navigate  enter use  esc close",
    "\u2191\u2193 move  enter  esc",
    "enter use  esc close",
    "enter esc",
)

#: The hint under the prompt-history picker (ctrl+r).
HISTORY_HINT_VARIANTS: tuple[str, ...] = (
    "\u2191\u2193 navigate     enter recall     esc close",
    "\u2191\u2193 navigate  enter recall  esc close",
    "\u2191\u2193 move  enter  esc",
    "enter recall  esc close",
    "enter esc",
)

CATALOG_HINT_VARIANTS: tuple[str, ...] = (
    "\u2191\u2193 navigate     tab provider     enter use     esc close",
    "\u2191\u2193 navigate  tab provider  enter use  esc close",
    "\u2191\u2193 move  tab provider  enter  esc",
    "enter use  esc close",
    "enter esc",
)


def menu_hint(*, cols: int, variants: tuple[str, ...] = MENU_HINT_VARIANTS) -> str:
    """fx's picker hint line: the widest variant that fits, else the shortest."""
    t = _theme.current()
    hint = variants[-1]
    for candidate in variants:
        if ansi.width(candidate) <= cols:
            hint = candidate
            break
    return f"{t.dim_style}{ansi.clip(hint, cols)}{ansi.RESET}"


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


@dataclass
class PermissionsView:
    mode_name: str
    description: str
    grants: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)

    def panel(self, cols: int, max_rows: int) -> list[str]:
        t = _theme.current()
        rows = [
            f"  {ansi.BOLD}Permissions{ansi.DIM_OFF}",
            f"  Mode  {t.tag_style}{self.mode_name}{ansi.RESET}  "
            f"{t.dim_style}{self.description}{ansi.RESET}",
        ]
        for title, items in (("Configured rules", self.rules), ("Session grants", self.grants)):
            rows.append(f"  {t.dim_style}{title} ({len(items)}){ansi.RESET}")
            rows.extend(
                f"    {t.dim_style}•{ansi.RESET} {ansi.clip_ellipsis(item, max(1, cols - 7))}"
                for item in (items or ["(none)"])
            )
        rows.append(
            f"  {t.dim_style}/mode to change mode  ·  /permissions <mode> to set it{ansi.RESET}"
        )
        return [ansi.clip(row, cols) for row in rows[: max(1, max_rows)]]

    def hint(self, cols: int) -> str:
        return menu_hint(cols=cols, variants=("↑↓ scroll  esc close", "esc close"))


# -- mode picker panel -------------------------------------------------------


@dataclass
class ModePickerView:
    """Picker for switching permission modes: Ask, Auto."""

    modes: list[MenuItem] = field(default_factory=list)
    selected: int = 0
    current_mode: str = ""

    def panel(self, cols: int) -> list[str]:
        t = _theme.current()
        rows: list[str] = []
        rows.append(f"  {ansi.BOLD}Permission mode{ansi.DIM_OFF}")
        rows.append(f"  {t.dim_style}Choose how vx handles tool calls{ansi.RESET}")
        rows.append("")
        for index, mode in enumerate(self.modes):
            is_selected = index == self.selected
            is_current = mode.value == self.current_mode or mode.label[1:] == self.current_mode
            style = t.selected_completion_style if is_selected else t.dim_style
            marker = "❯ " if is_selected else "  "
            label = mode.label
            if is_current:
                label = f"{label} (current)"
            row = f"  {marker}{style}{label}{ansi.RESET}"
            if mode.detail:
                desc_col = min(cols, max(ansi.width(label) + 8, cols // 2))
                row = ansi.pad(row, desc_col)
                detail = ansi.clip_ellipsis(mode.detail, max(1, cols - desc_col))
                row += f"{t.dim_style}{detail}{ansi.RESET}"
            rows.append(ansi.clip(row, cols))
        return rows

    def hint(self, cols: int) -> str:
        t = _theme.current()
        variants = (
            "↑↓ navigate     enter select     esc close",
            "↑↓ move  enter  esc",
            "enter select  esc close",
        )
        for candidate in variants:
            if ansi.width(candidate) <= cols:
                return f"{t.dim_style}{candidate}{ansi.RESET}"
        return f"{t.dim_style}{variants[-1]}{ansi.RESET}"
