"""The in-chat ``todo`` tool block.

Two jobs, and they are not the same job. The pinned strip
(:mod:`vtx.tui.todo_panel`) answers "where is the plan right now"; this block
answers "what did that last call do". Printing the whole list on every call
answers neither well — a seven-step plan updated twenty times puts a hundred
and forty identical rows in the scrollback, and the three rows that were
actually news are buried among them.

So the block is a diff view by default: one line when a task starts, one when
it finishes, one when a task is added. The full list arrives only twice — when
the plan is created and when it is finished — and on ``ctrl+o``, which is the
same key that expands every other tool's output.

Layout::

    ● Todos  2/5 · completed 1, starting next
    │ ├─ ✓ Create IDemoTodoRepository interface
    │ └─ ⠹ Create DemoTodoRepository   Creating DemoTodoRepository impl…

Rows come from :mod:`vtx.tui.todo_ui`, the same renderer the pinned strip
uses, so the block and the strip never disagree about a glyph.
"""

from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.widgets import Label

from vtx.core.config import config
from vtx.tui import todo_ui
from vtx.tui.blocks import ToolBlock

#: Header note per transition, borrowed from Crush's todo renderer. The number
#: alone ("3/7") does not say whether the agent moved forward, so the header
#: says which way it moved.
_NOTE_STARTED = "starting task"
_NOTE_DONE = "completed {n}"
_NOTE_DONE_AND_STARTED = "completed {n}, starting next"
_NOTE_ALL_DONE = "completed all"


class TodoToolBlock(ToolBlock):
    DEFAULT_CSS = """
    TodoToolBlock #tool-output {
        padding: 0;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.add_class("todo-block")
        self._frame = 0

    # -- header ------------------------------------------------------------

    def _format_header(self, truncate: bool = True) -> Text:
        if self._awaiting_approval or self._ask_dialog is not None:
            return super()._format_header(truncate=truncate)

        colors = config.ui.colors
        accent = Style(color=colors.accent, bold=True)
        muted = Style(color=colors.muted)

        result = Text()
        result.append(f"{self._icon} ", style=accent)
        result.append("Todos", style=accent)

        summary = self._ui_summary or ""
        if summary:
            result.append("  ")
            result.append(summary, style=muted)

        note = self._status_note()
        if note:
            result.append(" · ")
            result.append(note, style=muted)

        if self._success is None:
            result.append(" ...", style=muted)
        return result

    def _status_note(self) -> str:
        """What this call did, in a few words: ``completed 1, starting next``."""
        delta = self._delta()
        if not delta:
            return ""
        done = len(delta.get("just_completed") or [])
        started = delta.get("just_started") is not None
        if delta.get("all_completed"):
            return _NOTE_ALL_DONE
        if delta.get("is_new"):
            return f"planned {delta.get('total') or 0}"
        if done and started:
            return _NOTE_DONE_AND_STARTED.format(n=done)
        if done:
            return _NOTE_DONE.format(n=done)
        if started:
            return _NOTE_STARTED
        return ""

    def _delta(self) -> dict:
        data = self.ui_data or {}
        delta = data.get("delta")
        return delta if isinstance(delta, dict) else {}

    # -- body --------------------------------------------------------------

    def _render_result_output(self) -> None:
        try:
            output = self.query_one("#tool-output", Label)
        except Exception:
            return

        data = self.ui_data or {}
        if data.get("error"):
            # An error still renders the list so the reader can see what the
            # call was trying to do to which task, but in the failure colour —
            # a list drawn in the success palette reads as a result.
            output.update(self._render_error(data["error"]))
        elif data.get("tasks") is not None:
            output.update(self._render_body(data))
        else:
            super()._render_result_output()
            return

        self.remove_class("-compact")
        self.add_class("-with-details")
        output.remove_class("-hidden")
        output.remove_class("-details")
        output.remove_class("-diff-output")

    def _render_error(self, error: str) -> Text:
        style = Style(color=config.ui.colors.failed)
        return Text("todo: ", style=style) + Text(str(error), style=style)

    def _render_body(self, data: dict) -> Text:
        """Collapsed shows the delta, expanded shows the whole plan.

        ``ui_data`` holds plain dicts rather than ``TaskRecord``s, so they are
        re-wrapped before rendering — the renderer only reads attributes, and
        re-wrapping keeps it free of any dependency on the tool module.
        """
        tasks = [_Record(t) for t in data.get("tasks") or []]
        width = max(20, self._content_width())
        delta = data.get("delta") or {}
        if self._expanded:
            return todo_ui.render_list(tasks, width=width, frame=self._frame, expanded=True)
        return todo_ui.render_delta(delta, tasks, width=width, frame=self._frame)

    def _content_width(self) -> int:
        try:
            output = self.query_one("#tool-output", Label)
            for source in (output.size.width, output.content_size.width, self.size.width):
                if source:
                    return max(20, source - 2)
        except Exception:
            pass
        return 80

    def on_resize(self, event) -> None:
        del event
        if self.ui_data:
            self._render_result_output()


class _Record:
    """A ``TaskRecord`` rebuilt from the session-safe dict in ``ui_data``.

    ``ui_data`` crosses a JSON boundary on the way to the block, so the rows
    arrive as dicts. Rather than teach the renderer about both shapes, the
    block adapts once here.
    """

    __slots__ = ("active_form", "blocked_by", "description", "id", "owner", "status", "subject")

    def __init__(self, raw: dict) -> None:
        self.id = raw.get("id", 0)
        self.subject = raw.get("subject") or ""
        self.description = raw.get("description")
        self.active_form = raw.get("activeForm") or raw.get("active_form")
        self.status = raw.get("status", "pending")
        self.blocked_by = raw.get("blockedBy") or raw.get("blocked_by") or []
        self.owner = raw.get("owner")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"_Record(id={self.id!r}, status={self.status!r}, subject={self.subject!r})"
