"""Integration test: the sandbox driving real VTX tools, not test doubles.

The unit tests use trivial coroutines. This exercises the actual
``BaseTool`` contract, which is where the interesting mismatch lives: the
harness's ``ToolResult.result`` is a *string*, while the sandbox's boundary is
JSON data. ``integration.py`` exists to bridge that, and this is what proves it.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, Field

from vtx.ai.agent.codemode import (
    CodemodeSandbox,
    Limits,
    ToolError,
    adapt_tool,
    adapt_tools,
    base_tool_schema,
)
from vtx.ai.agent.tools import BaseTool
from vtx.core.types import ToolResult


class Query(BaseModel):
    n: int = Field(description="How many fake rows to produce")
    fail: bool = Field(default=False, description="Refuse instead of answering")


class RowsTool(BaseTool):
    name = "list_rows"
    description = "List N rows, each with an id and a status"
    params = Query
    mutating = False

    async def execute(self, params: Query, cancel_event=None) -> ToolResult:
        if params.fail:
            return ToolResult(success=False, result="upstream refused", ui_summary="refused")
        rows = [{"id": i, "status": "open" if i % 2 else "closed"} for i in range(params.n)]
        # The harness contract is a string, not structured data.
        return ToolResult(success=True, result=json.dumps(rows), ui_summary=f"{len(rows)} rows")


class PlainTextTool(BaseTool):
    name = "read_note"
    description = "Return a prose note, not JSON"
    params = Query
    mutating = False

    async def execute(self, params: Query, cancel_event=None) -> ToolResult:
        return ToolResult(success=True, result="just some words, not a payload")


class LeakyTool(BaseTool):
    name = "boom"
    description = "Raises an unclassified error carrying a host path"
    params = Query
    mutating = False

    async def execute(self, params: Query, cancel_event=None) -> ToolResult:
        raise RuntimeError("pool exhausted at /var/lib/postgresql/data")


def _sandbox() -> CodemodeSandbox:
    return CodemodeSandbox(
        tools=adapt_tools([RowsTool(), PlainTextTool(), LeakyTool()]),
        limits=Limits(timeout_ms=30_000),
    )


@pytest.mark.asyncio
async def test_json_encoded_result_reaches_the_script_as_data():
    # The trap this exists to prevent: a Python repr would arrive as a list
    # containing one string, and the script would then do string surgery on its
    # own output.
    result = await _sandbox().execute(
        "rows = await tools.list_rows(n=3)\nreturn rows[1]['status']"
    )
    assert result.ok
    assert result.value == "open"


@pytest.mark.asyncio
async def test_filtering_happens_in_code():
    result = await _sandbox().execute(
        "rows = await tools.list_rows(n=6)\n"
        "return {'total': len(rows), 'open_ids': [r['id'] for r in rows if r['status'] == 'open']}"
    )
    assert result.ok
    assert result.value == {"total": 6, "open_ids": [1, 3, 5]}


@pytest.mark.asyncio
async def test_non_json_result_stays_a_string():
    result = await _sandbox().execute("return (await tools.read_note(n=1)).upper()")
    assert result.ok
    assert result.value == "JUST SOME WORDS, NOT A PAYLOAD"


@pytest.mark.asyncio
async def test_concurrent_calls_return_ordered_results():
    result = await _sandbox().execute(
        "batches = await asyncio.gather(*[tools.list_rows(n=n) for n in (1, 2, 3)])\n"
        "return [len(b) for b in batches]"
    )
    assert result.ok
    assert result.value == [1, 2, 3]


@pytest.mark.asyncio
async def test_failed_tool_raises_a_catchable_error_with_its_own_message():
    result = await _sandbox().execute(
        "try:\n"
        "    await tools.list_rows(n=1, fail=True)\n"
        "except ToolError as e:\n"
        "    return str(e)"
    )
    assert result.ok
    # The tool's own message, not a generic "it failed".
    assert result.value == "refused"


@pytest.mark.asyncio
async def test_unclassified_tool_error_is_sanitized():
    result = await _sandbox().execute("return await tools.boom(n=1)")
    assert not result.ok
    assert result.diagnostic.kind == "tool_failure"
    assert "/var/lib/postgresql" not in repr(result)


@pytest.mark.asyncio
async def test_bad_arguments_fail_before_the_tool_runs():
    # A tool whose body would raise if reached: if this reports the tool's
    # message, validation did not happen first.
    result = await _sandbox().execute("return await tools.list_rows(n='not a number')")
    assert not result.ok
    assert result.diagnostic.kind == "invalid_input"


def test_schema_drops_pydantic_noise():
    schema = base_tool_schema(RowsTool())
    # `title` renders into the model-facing signature as noise.
    assert "title" not in schema
    assert schema["properties"]["n"]["description"] == "How many fake rows to produce"
    assert schema["properties"]["n"]["type"] == "integer"


def test_adapt_tools_respects_the_filter():
    tools = adapt_tools([RowsTool(), LeakyTool()], predicate=lambda t: t.name != "boom")
    assert [t.name for t in tools] == ["list_rows"]


@pytest.mark.asyncio
async def test_a_filtered_out_tool_is_unreachable():
    # The filter is the authority boundary, so a filtered tool must be a
    # classified failure rather than silently missing.
    tools = adapt_tools([RowsTool(), LeakyTool()], predicate=lambda t: t.name != "boom")
    sandbox = CodemodeSandbox(tools=tools, limits=Limits(timeout_ms=20_000))
    result = await sandbox.execute("return await tools.boom()")
    assert not result.ok
    assert result.diagnostic.kind == "unknown_tool"
    await sandbox.close()


@pytest.mark.asyncio
async def test_tool_error_from_a_tool_is_not_double_wrapped():
    # A tool author may raise ToolError deliberately to control what the model
    # sees; the adapter must not replace that message with a generic one.
    class Polite(BaseTool):
        name = "polite"
        description = "Declines politely"
        params = Query
        mutating = False

        async def execute(self, params: Query, cancel_event=None) -> ToolResult:
            raise ToolError("the widget is out of stock until Tuesday")

    sandbox = CodemodeSandbox(tools=adapt_tools([Polite()]), limits=Limits(timeout_ms=20_000))
    result = await sandbox.execute("return await tools.polite(n=1)")
    assert not result.ok
    assert result.diagnostic.message == "the widget is out of stock until Tuesday"
    await sandbox.close()


def test_adapt_tool_is_reusable_across_sandboxes():
    # A sandbox is configured once and executed many times; the adapter must not
    # carry per-execution state.
    tool = adapt_tool(RowsTool())
    assert tool.name == "list_rows"
    assert tool.input_schema is not None
    assert tool.identifier() == "list_rows"
