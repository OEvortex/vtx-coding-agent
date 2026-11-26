"""The sidebar: the plan the model is following, and the terminals it left open.

Two sections, each foldable, each sized to what is left of the panel. Headers are
a grid -- the name on the left, the count on the right -- and a chevron is the
whole fold affordance. Rows that open something end in a chevron, so the
interactivity sits on the rows instead of in a sentence under them.
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
class JobRow:
    """A background command as the panel draws it.

    The registry owns the process; this is only the four things a row needs, so
    the sidebar does not have to import the tool layer to draw a list.
    """

    id: str
    command: str
    status: str = "running"
    label: str = ""


@dataclass
class Sidebar:
    """The panel: the plan on top, the terminals the run left open below."""

    todos: list[Todo] = field(default_factory=list)
    jobs: list[JobRow] = field(default_factory=list)
    #: The disclosures: each section's open state, and the row that folds it.
    todos_open: bool = True
    jobs_open: bool = True
    tasks_header_row: int | None = None
    jobs_header_row: int | None = None
    #: Screen row -> job id, so a click opens that terminal.
    job_hits: dict[int, str] = field(default_factory=dict)

    def has_content(self) -> bool:
        """Anything worth a column: a plan, or a command still running."""
        return bool(self.todos or self.jobs)

    def rows(self, width: int, height: int) -> list[str]:
        """Exactly ``height`` rows: the plan, the terminals, then air."""
        t = theme.current()
        body = max(0, height - 1)
        # A folded section keeps its header and one line; an open plan needs a
        # header, its rail and one task before the rows are worth spending. No
        # plan at all is no section at all: the terminals get the whole panel.
        floor = 0 if not self.todos else (2 if not self.todos_open else 4)
        jobs_h = min(self._jobs_height(), max(0, body - floor))
        jobs = self._terminals(width, jobs_h)
        self.job_hits = {}
        if self.todos:
            out = self._tasks(width, body - len(jobs))
        else:
            self.tasks_header_row = None
            out = []
        out.extend(self._terminals(width, jobs_h, base=len(out)))
        while len(out) < height - 1:
            out.append("")
        hint = "ctrl+g fold \u00b7 click \u203a"
        if ansi.width(hint) > width:
            # A narrow panel says the key and leaves the rest to the chevron.
            hint = "ctrl+g fold"
        out.append(f"{t.dim_style}{ansi.clip_ellipsis(hint, width)}{ansi.RESET}")
        return out[:height]

    # -- chrome ------------------------------------------------------------

    def _head(self, label: str, meta: str, width: int, open_: bool, meta_style: str = "") -> str:
        """``▾ PLAN                    2/5``: the section's name, then its count.

        The chevron is the whole affordance: it says this line opens something
        and which way it is pointing now.
        """
        t = theme.current()
        chevron = "▾" if open_ else "▸"
        text = f"{t.hint_style}{ansi.BOLD}{label.upper()}{ansi.DIM_OFF}"
        row = f"{t.dim_style}{chevron}{ansi.RESET} {text}"
        if not meta:
            return ansi.clip(row, width)
        row = ansi.pad(row, max(1, width - ansi.width(meta)))
        return ansi.clip(f"{row}{meta_style or t.dim_style}{meta}{ansi.RESET}", width)

    @staticmethod
    def _rail(width: int, done: int, total: int) -> str:
        """A hairline of progress: filled in the text colour, the rest dim."""
        t = theme.current()
        if width < 6 or total <= 0:
            return ""
        filled = round(width * done / total)
        return (
            f" {t.hint_style}{'━' * filled}{t.dim_style}{'─' * (width - filled - 1)}{ansi.RESET}"
        )

    # -- the plan ---------------------------------------------------------

    def _tasks(self, width: int, height: int) -> list[str]:
        t = theme.current()
        marks = {
            "done": ("✓", t.dim_style),
            "active": ("▸", t.tag_style),
            "pending": ("○", t.dim_style),
            "blocked": ("⊘", t.dim_style),
        }
        done = sum(1 for todo in self.todos if todo.status == "done")
        out = [self._head("Plan", f"{done}/{len(self.todos)}", width, self.todos_open)]
        self.tasks_header_row = 0
        if not self.todos_open:
            # Folded, the section keeps the one line that is the point of it:
            # what the run is doing now. The count is already in the header.
            active = next((todo for todo in self.todos if todo.status == "active"), None)
            if active is not None and height >= 3:
                first = ansi.wrap(active.text, max(4, width - 4))[0]
                out.append(
                    ansi.clip(
                        f" {t.tag_style}▸{ansi.RESET} {t.hint_style}{first}{ansi.RESET}", width
                    )
                )
            return out
        if height >= 5:
            out.append(self._rail(width, done, len(self.todos)))
        # One row is reserved for the hint, and `more…` needs one of its own.
        room = max(0, height - len(out) - 1)

        def chunk_for(todo: Todo) -> list[str]:
            glyph, style = marks.get(todo.status, ("○", t.dim_style))
            label_style = t.hint_style if todo.status == "active" else t.dim_style
            lines = ansi.wrap(todo.text, max(4, width - 4))
            rows: list[str] = []
            for index, line in enumerate(lines):
                if index:
                    lead = f" {t.dim_style}│{ansi.RESET} " if todo.status == "active" else "   "
                else:
                    lead = f" {style}{glyph}{ansi.RESET} "
                rows.append(ansi.clip(f"{lead}{label_style}{line}{ansi.RESET}", width))
            return rows

        shown = 0
        for index, todo in enumerate(self.todos):
            chunk = chunk_for(todo)
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

    # -- the background terminals -----------------------------------------

    def _jobs_height(self) -> int:
        """Rows the section wants: a header, and one row per job when open."""
        if not self.jobs:
            return 0
        return len(self.jobs) + 2 if self.jobs_open else 2

    def _terminals(self, width: int, height: int, base: int = 0) -> list[str]:
        """One row per job: what is running, and how the rest ended.

        The count on the header is running/total, because the question a person
        asks a job list is "is anything still going".
        """
        if not self.jobs or height < 2:
            return []
        t = theme.current()
        running = sum(1 for job in self.jobs if job.status == "running")
        rows = ["", self._head("Terminals", f"{running}/{len(self.jobs)}", width, self.jobs_open)]
        self.jobs_header_row = base + len(rows) - 1
        if not self.jobs_open:
            return rows
        if height < 3:
            # A header with nothing under it is not a list: show no section.
            self.jobs_header_row = None
            return []
        # The `more…` line only costs a row when something is actually cut.
        room = max(1, height - len(rows))
        if len(self.jobs) > room:
            room -= 1
        shown = 0
        for job in self.jobs:
            if shown >= room:
                break
            self.job_hits[base + len(rows)] = job.id
            rows.append(self._job_row(job, width))
            shown += 1
        if shown < len(self.jobs):
            rows.append(f"{t.dim_style}{len(self.jobs) - shown} more…{ansi.RESET}")
        return rows

    def _job_row(self, job: JobRow, width: int) -> str:
        t = theme.current()
        glyph, style = {
            "running": ("●", t.tag_style),
            "done": ("✓", t.dim_style),
            "failed": ("✕", t.red_style),
            "stopped": ("⊘", t.red_style),
        }.get(job.status, ("·", t.dim_style))
        meta = f"{job.label}  \u203a"
        col = ansi.width(meta) + 1
        name = ansi.clip_ellipsis(job.command, max(6, width - col - 4))
        row = f" {style}{glyph}{ansi.RESET} {t.hint_style}{name}{ansi.RESET}"
        row = ansi.pad(row, max(1, width - col))
        return ansi.clip(f"{row}{t.dim_style}{meta}{ansi.RESET}", width)
