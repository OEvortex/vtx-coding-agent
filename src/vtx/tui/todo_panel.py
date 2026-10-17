"""The pinned ``Todos`` panel.

A standing view of the session's todo list, pinned above the editor so it
stays visible while the chat scrolls behind it, the same way the Agents
panel stays visible for sub-agents.

Layout (the sub-agent strip's grammar, not a second one)::

    ● Todos                            ████░░░░░░ 2/5
    │ ├─ ✓ Create DemoTodo domain entity
    │ ├─ ✓ Create IDemoTodoRepository interface
    │ ├─ ⠹ Create DemoTodoRepository   Creating DemoTodoRepository impl…
    │ ├─ ⊘ Write unit and integration tests   ← #3
    │ └─ ○ Update documentation

The rows, the gutter and the header all come from
:mod:`vtx.tui.agents_panel` and :mod:`vtx.tui.todo_ui`. Two strips stacked
above the editor with two different shapes read as two unrelated widgets;
these are one panel with two rows of state in it.

The list is capped at :data:`~vtx.tui.todo_ui.MAX_ROWS` and the rest collapse
to ``+N more``. This strip sits above the input box, not in the scrollback, so
it gives up rows rather than pushing the prompt off screen.
"""

from __future__ import annotations

from rich.text import Text
from textual.timer import Timer
from textual.widgets import Static

from vtx.coding_agent.tools.todo import current_todo_tool
from vtx.tui import todo_ui

#: Cadence for the spinner on the running row. Matches the sub-agent strip, so
#: both panels animate at the same rate and read as one interface.
TICK_SECONDS = 0.12

#: Repaint cadence when nothing is running. Nothing animates here; the strip
#: only has to notice when the last task finishes so it can update.
IDLE_TICK_SECONDS = 0.5


class TodoPanel(Static):
    DEFAULT_CSS = """
    TodoPanel {
        display: none;
        height: auto;
        padding: 0 1;
    }
    TodoPanel.-visible {
        display: block;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._frame = 0
        self._timer: Timer | None = None
        self._timer_interval = 0.0

    def on_mount(self) -> None:
        self.refresh_panel()

    def on_unmount(self) -> None:
        self._stop_timer()

    def on_resize(self, event) -> None:
        del event
        self.refresh_panel()

    def _start_timer(self, interval: float) -> None:
        # Textual's Timer has no public re-interval, so swapping the timer is
        # how the cadence changes.
        if self._timer is not None:
            if self._timer_interval == interval:
                return
            self._stop_timer()
        self._timer_interval = interval
        self._timer = self.set_interval(interval, self._on_tick)

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
            self._timer_interval = 0.0

    def _on_tick(self) -> None:
        self._frame += 1
        self.refresh_panel()

    @property
    def counts(self) -> tuple[int, int]:
        """``(completed, total)`` for callers that mirror this on the status line."""
        tool = current_todo_tool()
        return todo_ui.counts(getattr(tool, "_tasks", None) or [])

    def refresh_panel(self) -> None:
        """Re-read the todo tool and repaint (or hide) the strip.

        The timer only runs fast while a task is in progress, because that is
        the only state that animates. Parking it on the idle cadence instead of
        stopping it means the strip still updates the moment the last task is
        marked done, without spinning a widget that has nothing to show.
        """
        tool = current_todo_tool()
        tasks = getattr(tool, "_tasks", None) or []
        live = todo_ui.active_tasks(tasks)
        if not live:
            self.remove_class("-visible")
            self.update(Text(""))
            self._stop_timer()
            return

        if not self.has_class("-visible"):
            self.add_class("-visible")
        # The cadence follows the state rather than being set once on mount: a
        # task that starts ten minutes into a session still gets the fast timer,
        # and finishing the last one drops it back to the idle repaint.
        self._start_timer(
            TICK_SECONDS if todo_ui.current_task(tasks) is not None else IDLE_TICK_SECONDS
        )

        self.update(todo_ui.render_strip(tasks, width=self._content_width(), frame=self._frame))

    def _content_width(self) -> int:
        for source in (self.size.width, self.content_size.width, self.container_size.width):
            if source:
                # DEFAULT_CSS applies ``padding: 0 1`` -> two cells of chrome.
                return max(10, source - 2)
        return 80
