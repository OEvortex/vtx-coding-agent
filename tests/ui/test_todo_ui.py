"""Tests for the todo surfaces: the pinned strip and the in-chat block body.

Both build from :mod:`vtx.tui.todo_ui`, so the renderers are tested here once
rather than twice through two widgets. The widget-level tests only cover what
the widget itself adds: the hide/show class, the spinner timer, and the
collapse/expand split.
"""

import pytest
from rich.cells import cell_len

from vtx.coding_agent.tools.todo import TaskRecord
from vtx.tui import todo_ui
from vtx.tui.todo_block import _Record
from vtx.tui.todo_ui import (
    BAR_CELLS,
    GLYPH_BLOCKED,
    GLYPH_COMPLETED,
    GLYPH_PENDING,
    blocked_ids,
    counts,
    current_task,
    progress_bar,
    render_delta,
    render_list,
    render_strip,
    status_glyph,
    task_row,
)


def _task(task_id: int, subject: str, status: str = "pending", **kwargs) -> TaskRecord:
    return TaskRecord(id=task_id, subject=subject, status=status, **kwargs)


@pytest.fixture
def plan() -> list[TaskRecord]:
    return [
        _task(1, "Create DemoTodo domain entity", "completed"),
        _task(2, "Create IDemoTodoRepository interface", "completed"),
        _task(
            3,
            "Create DemoTodoRepository in Infrastructure",
            "in_progress",
            activeForm="Creating DemoTodoRepository implementation",
        ),
        _task(4, "Write unit and integration tests", blocked_by=[3]),
        _task(5, "Update the documentation"),
    ]


# -- counts and classification ------------------------------------------------


def test_counts_ignores_tombstoned_tasks(plan: list[TaskRecord]) -> None:
    plan.append(_task(6, "Deleted task", "deleted"))
    assert counts(plan) == (2, 5)


def test_current_task_is_the_single_running_one(plan: list[TaskRecord]) -> None:
    assert current_task(plan).id == 3


def test_blocked_needs_an_unfinished_dependency(plan: list[TaskRecord]) -> None:
    # #4 waits on #3, which is still running.
    assert blocked_ids(plan) == {4}

    plan[2].status = "completed"
    # The blocker landing clears the marker.
    assert blocked_ids(plan) == set()


def test_a_completed_task_is_not_blocked_by_anything(plan: list[TaskRecord]) -> None:
    plan[0].blocked_by = [4]
    assert 1 not in blocked_ids(plan)


# -- glyphs -------------------------------------------------------------------


def test_glyphs_agree_with_the_sub_agent_strip() -> None:
    """One legend has to cover both panels stacked above the editor."""
    assert GLYPH_COMPLETED == "✓"
    assert GLYPH_PENDING == "○"
    assert status_glyph("in_progress", frame=3)[0] == todo_ui.SPINNER[3]


def test_the_running_row_animates_and_the_others_do_not(plan: list[TaskRecord]) -> None:
    first = status_glyph("in_progress", frame=0)[0]
    second = status_glyph("in_progress", frame=1)[0]
    assert first != second
    # A static row must not change between frames, or the strip would flicker
    # on rows that represent nothing in motion.
    assert status_glyph("pending", frame=0)[0] == status_glyph("pending", frame=7)[0]
    assert status_glyph("completed", frame=0)[0] == status_glyph("completed", frame=7)[0]


def test_blocked_is_a_distinct_glyph_from_pending() -> None:
    """Pending-because-blocked and pending-because-next are different states."""
    assert status_glyph("pending", frame=0, blocked=True)[0] == GLYPH_BLOCKED
    assert status_glyph("pending", frame=0, blocked=False)[0] == GLYPH_PENDING


def test_dependency_badge_avoids_emoji_presentation_codepoints(plan: list[TaskRecord]) -> None:
    """A cluster ending in U+FE0F measures two cells and renders one.

    Terminals that disagree with the width calculation repaint the line on
    every frame, which reads as flicker. Crush documents the same trap.
    """
    row = task_row(plan[3], width=80, blocked=True).plain
    assert "← #3" in row
    assert "⛓" not in row


# -- progress bar -------------------------------------------------------------


def test_progress_bar_fill_matches_the_ratio() -> None:
    bar = progress_bar(5, 10, width=10).plain
    assert bar == "█████░░░░░"


def test_progress_bar_keeps_one_cell_of_fill_for_a_started_plan() -> None:
    """An empty bar next to ``1/9`` reads as "nothing done" rather than "a bit"."""
    assert progress_bar(1, 9, width=10).plain.startswith("█")


def test_progress_bar_is_empty_before_any_task_exists() -> None:
    assert progress_bar(0, 0, width=10).plain == ""


def test_progress_bar_never_overflows_its_width() -> None:
    assert cell_len(progress_bar(10, 10, width=BAR_CELLS).plain) == BAR_CELLS


# -- the strip ----------------------------------------------------------------


def test_strip_leads_with_a_counter_and_a_bar(plan: list[TaskRecord]) -> None:
    lines = render_strip(plan, width=80).plain.splitlines()
    header = lines[0]
    assert header.strip().startswith("● Todos")
    assert "2/5" in header
    assert "█" in header


def test_strip_draws_one_row_per_task(plan: list[TaskRecord]) -> None:
    lines = render_strip(plan, width=80).plain.splitlines()
    assert len(lines) == 6  # header + five tasks
    for line in lines[1:]:
        assert line.startswith("│ ")


def test_strip_uses_the_same_branch_glyphs_as_the_agents_panel(plan: list[TaskRecord]) -> None:
    from vtx.tui.agents_panel import TREE_EDGE, TREE_LAST

    lines = render_strip(plan, width=80).plain.splitlines()
    assert f"│ {TREE_EDGE} " in lines[1]
    assert f"│ {TREE_LAST} " in lines[-1]


def test_strip_preserves_insertion_order(plan: list[TaskRecord]) -> None:
    """The plan is the order the model wrote, and a live strip must not reshuffle.

    Codex sorts the running row to the top and Crush sorts finished rows first.
    Both look better frozen and both reorder on every repaint, which is wrong
    for a strip that repaints several times a second.
    """
    body = render_strip(plan, width=80).plain
    positions = [body.index(f"task {n}") for n in range(1, 6) if f"task {n}" in body]
    assert positions == sorted(positions)


def test_strip_caps_rows_and_reports_the_rest(plan: list[TaskRecord]) -> None:
    lines = render_strip(plan, width=80, max_rows=2).plain.splitlines()
    assert sum(1 for line in lines if line.startswith("│ ├─ ") or "└─" in line) == 3
    assert lines[-1].strip().endswith("+3 more")


def test_strip_shows_the_running_task_as_a_verb_phrase(plan: list[TaskRecord]) -> None:
    body = render_strip(plan, width=100).plain
    running = next(line for line in body.splitlines() if "Infrastructure" in line)
    # The subject is the thing being planned; the active form is what is
    # happening right now, and the two read differently on screen.
    assert "Create DemoTodoRepository in Infrastructure" in running
    assert "Creating DemoTodoRepository im" in running


def test_strip_marks_a_blocked_task(plan: list[TaskRecord]) -> None:
    body = render_strip(plan, width=100).plain
    blocked_row = next(line for line in body.splitlines() if "integration tests" in line)
    assert GLYPH_BLOCKED in blocked_row
    assert "← #3" in blocked_row


def test_strip_rows_never_exceed_the_width_they_were_given(plan: list[TaskRecord]) -> None:
    for width in (40, 60, 80, 120):
        for line in render_strip(plan, width=width).plain.splitlines():
            assert cell_len(line) <= width, f"{width=} {line!r}"


def test_strip_ellipsizes_a_subject_that_cannot_fit() -> None:
    long_task = _task(1, "Do a very long thing " * 20)
    line = task_row(long_task, width=40, last=True).plain
    assert cell_len(line) <= 40
    assert line.endswith("…")


def test_empty_plan_renders_no_rows() -> None:
    assert render_strip([], width=80).plain.splitlines()[0].strip().startswith("● Todos")
    assert counts([]) == (0, 0)


# -- the in-chat list ---------------------------------------------------------


def test_collapsed_list_is_one_row_per_task_without_ids(plan: list[TaskRecord]) -> None:
    body = render_list(plan, width=100).plain
    assert "#1" not in body
    assert "Create DemoTodo domain entity" in body


def test_expanded_list_adds_ids_and_a_bar(plan: list[TaskRecord]) -> None:
    lines = render_list(plan, width=100, expanded=True).plain.splitlines()
    assert "2/5 completed" in lines[0]
    assert "#1 Create DemoTodo domain entity" in lines[1]


def test_expanded_list_includes_descriptions(plan: list[TaskRecord]) -> None:
    plan[0].description = "one pydantic model and its migration"
    body = render_list(plan, width=120, expanded=True).plain
    assert "one pydantic model and its migration" in body


def test_expanded_list_rows_respect_the_width(plan: list[TaskRecord]) -> None:
    for width in (50, 80):
        for line in render_list(plan, width=width, expanded=True).plain.splitlines():
            assert cell_len(line) <= width


# -- the delta ----------------------------------------------------------------


def test_creation_prints_the_whole_plan(plan: list[TaskRecord]) -> None:
    body = render_delta({"is_new": True}, [_Record(_dump(t)) for t in plan], width=100).plain
    assert len(body.splitlines()) == 5


def test_completion_prints_the_whole_plan_and_a_note(plan: list[TaskRecord]) -> None:
    for task in plan:
        task.status = "completed"
    delta = {"is_new": False, "all_completed": True}
    body = render_delta(delta, [_Record(_dump(t)) for t in plan], width=100).plain
    assert "✓ all tasks complete" in body


def test_an_intermediate_update_prints_only_the_changed_rows(plan: list[TaskRecord]) -> None:
    """The scrollback should carry news, not a copy of the strip every call."""
    delta = {"just_completed": [1], "just_started": 3, "just_added": [], "total": 5}
    body = render_delta(delta, [_Record(_dump(t)) for t in plan], width=100).plain
    lines = body.splitlines()
    assert len(lines) == 2
    assert "Create DemoTodo domain entity" in lines[0]
    assert "Infrastructure" in lines[1]
    # The rows that did not change are not repeated.
    assert "IDemoTodoRepository interface" not in body


def test_a_new_task_prints_one_row(plan: list[TaskRecord]) -> None:
    delta = {"just_added": [5], "just_completed": [], "just_started": None}
    body = render_delta(delta, [_Record(_dump(t)) for t in plan], width=100).plain
    assert len(body.splitlines()) == 1
    assert "Update the documentation" in body


def test_a_metadata_only_edit_prints_nothing(plan: list[TaskRecord]) -> None:
    delta = {"just_added": [], "just_completed": [], "just_started": None}
    assert render_delta(delta, [_Record(_dump(t)) for t in plan], width=100).plain == ""


def test_the_running_row_is_drawn_last(plan: list[TaskRecord]) -> None:
    """The eye finishes on the animating row, so it goes at the bottom."""
    delta = {"just_completed": [1, 2], "just_started": 3, "just_added": []}
    lines = render_delta(delta, [_Record(_dump(t)) for t in plan], width=100).plain.splitlines()
    assert "Infrastructure" in lines[-1]


def test_delta_rows_respect_the_width(plan: list[TaskRecord]) -> None:
    delta = {"just_completed": [1], "just_started": 3}
    for width in (40, 60, 80):
        for line in render_delta(
            delta, [_Record(_dump(t)) for t in plan], width=width
        ).plain.splitlines():
            assert cell_len(line) <= width


def _dump(task: TaskRecord) -> dict:
    return task.model_dump(by_alias=True, exclude_none=True)
