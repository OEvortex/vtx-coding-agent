"""The sidebar: the plan the model is following, and the files it has written.

Two sections, each foldable, each sized to what is left of the panel. It is a
list of sentences on purpose -- a glyph and a few words per row -- because the
transcript beside it already carries the detail.
"""

from dataclasses import dataclass, field

from vx.tui import ansi, theme

#: Below these the panel costs more than it shows: a canvas narrower than a diff
#: hunk, or a column so short that both sections come out as ``N more…``. Both
#: are floors, not preferences - the panel gives way first.
SIDEBAR_MIN_COLS = 92
SIDEBAR_MIN_ROWS = 18
SIDEBAR_MIN_WIDTH = 24
SIDEBAR_WIDTH = 30

#: A real minus reads as arithmetic; ruff's ambiguity rule wants it escaped.
MINUS = "\u2212"

#: The columns the panel and its rule need: the rule plus one space.
SIDEBAR_CHROME = 2


def sidebar_cols(cols: int, rows: int) -> int:
    """The panel's width, or 0 when the terminal is too small to hold both.

    The panel never takes more than a quarter of the width: a 30-column column
    beside a 60-column transcript is a panel nobody can read either side of.
    """
    if cols < SIDEBAR_MIN_COLS or rows < SIDEBAR_MIN_ROWS:
        return 0
    return max(SIDEBAR_MIN_WIDTH, min(SIDEBAR_WIDTH, cols // 4))


@dataclass
class Todo:
    """One row of the plan: what the model says it is doing."""

    text: str
    status: str = "pending"  # pending | active | done | blocked


@dataclass
class Touch:
    """One file the turn wrote, with the lines it added and removed."""

    path: str
    added: int = 0
    removed: int = 0


@dataclass
class Sidebar:
    """The panel: the plan on top, the files this turn wrote below."""

    todos: list[Todo] = field(default_factory=list)
    touches: list[Touch] = field(default_factory=list)
    todos_open: bool = True
    files_open: bool = True

    def has_content(self) -> bool:
        """Anything worth a column: a plan, or files this turn wrote."""
        return bool(self.todos or self.touches)

    def rows(self, width: int, height: int) -> list[str]:
        """Exactly ``height`` rows: two sections and a pinned key hint."""
        t = theme.current()
        body = max(0, height - 1)
        # The files are the news, so they are budgeted first; the plan keeps at
        # least its header, its rule and one task, and fills the rest.
        reserve = 4 if self.todos_open else 3
        if self.touches and self.files_open:
            budget = min(len(self.touches) + 3, max(0, body - reserve))
        elif self.touches:
            budget = min(2, max(0, body - reserve))
        else:
            budget = 0
        files = self._files(width, budget)
        out = self._tasks(width, body - len(files))
        out.extend(files)
        while len(out) < height - 1:
            out.append("")
        out.append(f"{t.dim_style}{ansi.clip_ellipsis(self._hint(), width)}{ansi.RESET}")
        return out[:height]

    def _hint(self) -> str:
        return "ctrl+t hides · ctrl+g folds"

    # -- the plan ---------------------------------------------------------

    def _tasks(self, width: int, height: int) -> list[str]:
        t = theme.current()
        marks = {
            "done": ("✓", t.green_style),
            "active": ("▸", t.tag_style),
            "pending": ("○", t.dim_style),
            "blocked": ("⊘", t.red_style),
        }
        done = sum(1 for todo in self.todos if todo.status == "done")
        count = f"{done}/{len(self.todos)}"
        mark = "▾" if self.todos_open else "▸"
        head = f"{ansi.BOLD}Tasks{ansi.DIM_OFF} {t.dim_style}{mark}{ansi.RESET}"
        out = [
            ansi.clip(
                ansi.pad(head, max(1, width - len(count))) + f"{t.dim_style}{count}{ansi.RESET}",
                width,
            )
        ]
        out.append(f"{t.dim_style}{'─' * width}{ansi.RESET}")
        if not self.todos_open:
            # Folded, the plan keeps the one line that is the point of it: what
            # the run is doing now. The count is already in the header.
            active = next((todo for todo in self.todos if todo.status == "active"), None)
            if active is not None and height >= 3:
                out.append(self._chunk_for(active, width, marks)[0])
            return out
        if not self.todos:
            if height >= 3:
                out.append(f"{t.dim_style}   no plan yet{ansi.RESET}")
            return out
        # One row is reserved for the hint, and `more…` needs one of its own.
        room = max(0, height - len(out) - 1)
        shown = 0
        for index, todo in enumerate(self.todos):
            chunk = self._chunk_for(todo, width, marks)
            remaining = len(self.todos) - index
            used = len(out) - 2
            if used + len(chunk) > room or (used + len(chunk) >= room and remaining > 1):
                break
            out.extend(chunk)
            shown += 1
        if shown < len(self.todos):
            # Say how much of the plan is off the bottom instead of pretending
            # the list ended, and never let that line eat the hint's row.
            if len(out) - 2 >= room and len(out) > 2:
                out.pop()
            out.append(f"{t.dim_style}{len(self.todos) - shown} more…{ansi.RESET}")
        return out

    def _chunk_for(self, todo: Todo, width: int, marks: dict[str, tuple[str, str]]) -> list[str]:
        glyph, style = marks.get(todo.status, ("○", theme.current().dim_style))
        t = theme.current()
        label_style = t.dim_style if todo.status == "done" else style
        lines = ansi.wrap(todo.text, max(4, width - 4))
        return [
            ansi.clip(
                (f" {style}{glyph}{ansi.RESET} " if index == 0 else "   ")
                + label_style
                + line
                + ansi.RESET,
                width,
            )
            for index, line in enumerate(lines)
        ]

    # -- the files the turn wrote ----------------------------------------

    def _files(self, width: int, height: int) -> list[str]:
        """``app.py  +12 -3`` per touched file, newest first, capped to fit.

        The counts are what the patch reported, so the panel says how much this
        turn changed without a diff and without a second source of truth.
        """
        # A blank and a header are all a closed section needs.
        if not self.touches or height < 2:
            return []
        t = theme.current()
        added = sum(touch.added for touch in self.touches)
        removed = sum(touch.removed for touch in self.touches)
        tail = f"+{added} {MINUS}{removed}"
        mark = "▾" if self.files_open else "▸"
        head = f"{ansi.BOLD}Files{ansi.DIM_OFF} {t.dim_style}{mark}{ansi.RESET}"
        row = ansi.pad(head, max(1, width - ansi.width(tail))) + f"{t.dim_style}{tail}{ansi.RESET}"
        rows = ["", ansi.clip(row, width)]
        if not self.files_open:
            return rows
        if height < 4:
            # Header with nothing under it is not a list: show no section.
            return []
        room = max(1, height - len(rows) - 1)
        shown = 0
        for touch in self.touches:
            if shown >= room:
                break
            rows.append(self._file_row(touch, width))
            shown += 1
        if shown < len(self.touches):
            rows.append(f"{t.dim_style}{len(self.touches) - shown} more…{ansi.RESET}")
        return rows

    def _file_row(self, touch: Touch, width: int) -> str:
        t = theme.current()
        counts = f"+{touch.added} {MINUS}{touch.removed}"
        # One cell of air between the name and the counts, whatever they cost.
        col = ansi.width(counts) + 1
        name = touch.path.rsplit("/", 1)[-1]
        label = ansi.clip_ellipsis(name, max(6, width - col - 3))
        row = f"  {t.hint_style}{label}{ansi.RESET}"
        row = ansi.pad(row, max(1, width - col))
        add = f"{t.diff_added_style}+{touch.added}{ansi.RESET}"
        rem = f"{t.diff_removed_style}{MINUS}{touch.removed}{ansi.RESET}"
        return ansi.clip(f"{row}{add} {rem}", width)
