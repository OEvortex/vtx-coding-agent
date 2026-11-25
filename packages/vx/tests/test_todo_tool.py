"""The todo tool: taking the shapes models send, and answering with a plan."""

import asyncio

from vx.tools.todo import MAX_TODOS, TodoParams, TodoTool


def plan(*items) -> list[dict[str, str]]:
    return TodoParams(todos=list(items)).normalized()


def test_bare_strings_are_a_plan():
    assert [item["text"] for item in plan("read it", "write it")] == ["read it", "write it"]


def test_every_name_for_the_text_is_accepted():
    # Models reach for task/title/description; refusing them is a retry.
    got = plan({"task": "one"}, {"title": "two"}, {"description": "three"}, {"content": "four"})
    assert [item["text"] for item in got] == ["one", "two", "three", "four"]


def test_every_name_for_the_status_is_accepted():
    got = plan(
        {"text": "a", "status": "in progress"},
        {"text": "b", "status": "in_progress"},
        {"text": "c", "status": "completed"},
        {"text": "d", "status": "TODO"},
        {"text": "e", "status": "stuck"},
    )
    assert [item["status"] for item in got] == ["active", "active", "done", "pending", "blocked"]


def test_an_unknown_status_is_pending_not_an_error():
    assert plan({"text": "a", "status": "moonlight"})[0]["status"] == "pending"
    assert plan({"text": "a", "status": None})[0]["status"] == "pending"


def test_a_single_item_need_not_be_a_list():
    assert [item["text"] for item in plan({"task": "only one"})] == ["only one"]


def test_blank_rows_are_dropped():
    assert plan("", "  ", {"text": ""}, {"text": "real"}) == [
        {"text": "real", "status": "pending"}
    ]


def test_the_plan_is_capped():
    assert len(plan(*[f"step {index}" for index in range(50)])) == MAX_TODOS


def test_the_tool_reports_the_plan_back_to_the_model():
    result = asyncio.run(TodoTool().execute(TodoParams(todos=["read", "write"])))
    assert result.success
    assert "[pending] read" in (result.result or "")
    assert result.ui_data == {
        "todos": [{"text": "read", "status": "pending"}, {"text": "write", "status": "pending"}]
    }


def test_the_summary_names_the_active_step():
    result = asyncio.run(
        TodoTool().execute(
            TodoParams(
                todos=[{"text": "read", "status": "done"}, {"text": "write", "status": "active"}]
            )
        )
    )
    assert result.ui_summary == "1/2 done · write"


def test_an_empty_plan_clears_the_panel():
    result = asyncio.run(TodoTool().execute(TodoParams(todos=[])))
    assert result.ui_data == {"todos": []}
    assert TodoTool().format_call(TodoParams(todos=[])) == "clear the plan"


def test_the_header_says_how_far_along_the_plan_is():
    tool = TodoTool()
    params = TodoParams(todos=[{"text": "a", "status": "done"}, {"text": "b"}])
    assert tool.format_call(params) == "1/2 steps"
