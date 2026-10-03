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

from vtx.agent.tools import BaseTool
from vtx.codemode import (
    CodemodeSandbox,
    Limits,
    ToolError,
    adapt_tool,
    adapt_tools,
    base_tool_schema,
)
from vtx.codemode.types import CodemodeTool
from vtx.protocol.types import ToolResult


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


# ---- structured results --------------------------------------------------


class RemoteTool(BaseTool):
    """Stands in for an MCP tool: its value is a ``CallToolResult``, not prose."""

    name = "remote"
    description = "Call a remote server"
    params = Query
    mutating = False

    def __init__(self) -> None:
        self._fail = False

    @property
    def output_schema(self):
        from vtx.mcp.tool import mcp_result_schema

        return mcp_result_schema({"type": "array", "items": {"type": "integer"}})

    async def execute(self, params: Query, cancel_event=None) -> ToolResult:
        value = {
            "content": [{"type": "text", "text": "upstream refused" if self._fail else "ok"}],
            "structuredContent": {"rows": [1, 2, 3]},
            "isError": self._fail,
        }
        # What a model would read is flattened and truncated; what a script gets
        # is the whole thing. Both are present on purpose.
        return ToolResult(success=not self._fail, result="ok", structured=value)


@pytest.mark.asyncio
async def test_a_structured_result_reaches_the_script_whole():
    tool = RemoteTool()
    sandbox = CodemodeSandbox(tools=[adapt_tool(tool)])
    result = await sandbox.execute(
        "r = await tools.remote(n=1)\n"
        "return {'rows': r['structuredContent']['rows'], 'text': r['content'][0]['text']}"
    )
    assert result.ok
    assert result.value == {"rows": [1, 2, 3], "text": "ok"}


@pytest.mark.asyncio
async def test_a_structured_failure_is_a_value_not_an_exception():
    # An MCP tool that fails usually says why. Raising would discard the only
    # text that explains it, and the script could not branch on the reason.
    tool = RemoteTool()
    tool._fail = True
    sandbox = CodemodeSandbox(tools=[adapt_tool(tool)])
    result = await sandbox.execute(
        "r = await tools.remote(n=1)\n"
        "return {'failed': r['isError'], 'why': r['content'][0]['text']}"
    )
    assert result.ok
    assert result.value == {"failed": True, "why": "upstream refused"}


def test_an_mcp_result_schema_renders_as_a_call_tool_result():
    # The declaration is the model's only description of what it gets back. A
    # bare `dict[str, Any]` would not say that structured data is there, or that
    # `isError` is there to be branched on.
    from vtx.codemode.declarations import render_signature

    text = render_signature(adapt_tool(RemoteTool()))
    # The tool's own declared output shape is spelled into the envelope, so the
    # model learns the value is typed rather than an opaque dict.
    assert "CallToolResult[list[int]]" in text


def test_the_mcp_types_preamble_appears_only_when_something_uses_it():
    from vtx.codemode.declarations import render_declarations
    from vtx.codemode.types import CodemodeTool

    plain = CodemodeTool(name="read", description="Read", execute=lambda a, s: None)

    # Nothing returns one, so the explanation of what a CallToolResult is would
    # be text the model pays for and never uses.
    assert "MCP tools return a CallToolResult" not in render_declarations([plain])[0]
    assert (
        "MCP tools return a CallToolResult"
        in render_declarations([plain, adapt_tool(RemoteTool())])[0]
    )


# ---- namespace grouping --------------------------------------------------


def test_the_catalog_groups_tools_by_namespace():
    from vtx.codemode.declarations import render_declarations
    from vtx.codemode.types import CodemodeTool

    tools = [
        CodemodeTool(name="read", description="Read a file", execute=lambda a, s: None),
        CodemodeTool(
            name="mcp__docs__search",
            description="Search the docs",
            execute=lambda a, s: None,
            namespace="mcp__docs",
            namespace_description="Documentation search.",
        ),
    ]
    text, complete = render_declarations(tools)
    assert complete
    assert "# mcp__docs (1 tool)" in text
    # The server's own description of its tools becomes the section header, so it
    # reaches the model without costing anything when no script is written.
    assert "Documentation search." in text


def test_a_tiny_budget_still_shows_every_namespace():
    # The property a flat cheapest-first pass does not have: with two servers,
    # the cheaper one must not use the whole budget and leave the other absent
    # with no sign it exists.
    from vtx.codemode.declarations import render_declarations
    from vtx.codemode.types import CodemodeTool

    def many(namespace: str, count: int) -> list[CodemodeTool]:
        return [
            CodemodeTool(
                name=f"{namespace}__tool_{i}",
                description="x" * 200,
                execute=lambda a, s: None,
                namespace=namespace,
            )
            for i in range(count)
        ]

    tools = many("mcp__cheap", 2) + many("mcp__dear", 20)
    text, complete = render_declarations(tools, budget_tokens=40)
    assert not complete
    # Both namespaces are present, and the empty one says it is empty rather
    # than vanishing.
    assert "# mcp__cheap" in text
    assert "# mcp__dear" in text
    assert "none shown" in text or "shown)" in text


def test_a_listed_and_callable_tool_split_survives_into_the_instructions():
    from vtx.codemode.types import CodemodeTool

    async def noop(args, signal):
        return None

    tools = [
        CodemodeTool(name="read", description="Read", execute=noop, listed=True),
        CodemodeTool(
            name="mcp__docs__rare",
            description="Rare",
            execute=noop,
            listed=False,
            namespace="mcp__docs",
        ),
    ]
    sandbox = CodemodeSandbox(tools=tools, listed=["read"], catalog_budget_tokens=2000)
    instructions = sandbox.instructions()
    # Callable but unlisted: the model has to be told that separately, because
    # "the list above is partial" does not explain a tool it was never shown.
    assert "mcp__docs__rare" not in instructions
    assert "1 further tool can be called but are not listed" in instructions
    # ...and it is still callable, or it would be lost rather than deferred.
    assert any(t.name == "mcp__docs__rare" for t in sandbox.tools)


@pytest.mark.asyncio
async def test_search_finds_a_tool_the_catalog_never_listed():
    # A tool that is callable but unlisted is the one a model has least reason
    # to know exists, so hiding it from the search that exists to surface it
    # would make it unreachable in practice rather than merely unadvertised.
    from vtx.codemode.types import CodemodeTool

    async def noop(args, signal):
        return None

    tools = [
        CodemodeTool(name="read", description="Read a file", execute=noop, listed=True),
        CodemodeTool(
            name="mcp__docs__rare",
            description="Rebuild the documentation index",
            execute=noop,
            listed=False,
            namespace="mcp__docs",
        ),
    ]
    sandbox = CodemodeSandbox(tools=tools, listed=["read"], catalog_budget_tokens=2000)
    result = await sandbox.execute(
        "f = await tools.search(query='rebuild documentation index')\n"
        "return [[m['name'], m['listed']] for m in f['matches']]"
    )
    assert result.ok
    # JSON, so a tuple would arrive as a list -- the same trap the store is
    # careful about, and the reason this asserts a list.
    assert result.value == [["mcp__docs__rare", False]]


@pytest.mark.parametrize(
    "schema",
    [
        {"content": [{"type": "text"}]},  # envelope, no structuredContent
        {"type": "object", "properties": {"n": {"type": "integer"}}},  # not one
        {},  # empty
        "oops",  # not even a schema object
        [1, 2],
        None,
    ],
)
def test_a_hostile_output_schema_degrades_instead_of_raising(schema):
    """An `output_schema` arrives from a third-party server, unvalidated.

    A tool declaring `output_schema: "oops"` must render as `Any`, not raise out
    of `render_signature` -- the declaration is built per turn, so a raise there
    would take down every request from a session with a badly-behaved server
    connected, rather than that one tool's description.
    """
    from vtx.codemode.declarations import render_signature

    async def noop(args, signal):
        return None

    text = render_signature(
        CodemodeTool(name="remote", description="R", execute=noop, output_schema=schema)
    )
    assert text.startswith("def tools.remote(")
    assert "-> " in text


def test_a_string_structured_content_still_renders_the_envelope():
    # Present but the wrong shape: the envelope is real, so it is named, and the
    # inner type falls back rather than inventing one.
    from vtx.codemode.declarations import render_signature
    from vtx.mcp.tool import mcp_result_schema

    async def noop(args, signal):
        return None

    text = render_signature(
        CodemodeTool(
            name="remote", description="R", execute=noop, output_schema=mcp_result_schema("oops")
        )
    )
    assert "-> CallToolResult:" in text
