"""The pinned ``Todos`` panel.

A standing view of the session's todo list, pinned above the editor so it
stays visible while the chat scrolls behind it, the same way the Agents
panel stays visible for sub-agents.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from vtx.coding_agent.tools.todo import current_todo_tool, format_todo_tree


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

    def refresh_panel(self) -> None:
        tool = current_todo_tool()
        tasks = getattr(tool, "_tasks", None) if tool is not None else None
        tasks = tasks or []
        active = [t for t in tasks if getattr(t, "status", "") != "deleted"]
        if not active:
            self.remove_class("-visible")
            self.update(Text(""))
            return
        if not self.has_class("-visible"):
            self.add_class("-visible")
        self.update(Text.from_markup(format_todo_tree(tasks)))
