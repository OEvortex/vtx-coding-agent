"""The session's todo list as a system-prompt section.

Opt-in via ``llm.system_prompt.todo_context``. The model already sees every
``todo`` tool result, but tool output scrolls away and compaction can drop it;
this section pins the live list where the model re-reads it every turn. It is
a snapshot rebuilt when the todo tool reports a change (the runtime registers
a refresh callback), so it can never disagree with the panel for long.
"""

from __future__ import annotations

_STATUS_GLYPHS = {"pending": "○", "in_progress": "◐", "completed": "✓", "deleted": "⊘"}


def build_todo_section() -> str:
    """Render the live todo list, or ``""`` when there is nothing to show."""
    # Lazy import: coding_agent.tools.todo pulls in the plugin machinery, and
    # prompts is a lower layer that must not depend on it at import time.
    from vtx.coding_agent.tools.todo import current_todo_tool

    tool = current_todo_tool()
    tasks = [
        t for t in (getattr(tool, "_tasks", None) or []) if getattr(t, "status", "") != "deleted"
    ]
    if not tasks:
        return ""

    lines = [
        "# Todo list",
        "The session's current todo list. Keep it accurate with the todo tool.",
    ]
    for task in tasks:
        status = getattr(task, "status", "pending")
        glyph = _STATUS_GLYPHS.get(status, "○")
        subject = getattr(task, "subject", "") or ""
        lines.append(f"- {glyph} #{getattr(task, 'id', '?')} {subject}")
    return "\n".join(lines)


__all__ = ["build_todo_section"]
