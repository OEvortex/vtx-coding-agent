"""The ``codemode`` tool as the model meets it.

Two things are under test that the sandbox tests do not cover: that the tool
wires the harness's tool set into the sandbox correctly, and that a failure
tells the model something it can act on. The sandbox's own confinement is
tested in ``test_codemode.py``.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, Field

from vtx.ai.agent.tools import get_all_tools, get_default_tools, get_tool_definitions
from vtx.ai.agent.tools.codemode import CODEMODE_TOOL_NAME, CodemodeParams, CodemodeTool
from vtx.core.types import ToolResult


class Echo(BaseModel):
    value: str = Field(description="What to echo")


class EchoTool:
    """A minimal stand-in, registered the way the real ones are."""

    name = "echo"
    description = "Echo a string back"
    params = Echo
    mutating = False

    async def execute(self, params: Echo, cancel_event=None) -> ToolResult:
        return ToolResult(success=True, result=json.dumps({"echoed": params.value}))


@pytest.fixture
def tool() -> CodemodeTool:
    return CodemodeTool()


@pytest.fixture
def echo_tool() -> EchoTool:
    """Register a throwaway tool so a script has something real to call.

    Registered for the test's duration only: the registry is process-global, and
    a leaked entry would show up in every other test that inspects the tool set.
    """
    from vtx.ai.agent.tools import register_tool, unregister_tool

    tool = EchoTool()
    register_tool(tool, is_default=True)
    yield tool
    unregister_tool("echo")


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_codemode_is_registered_and_default_on():
    # Default-on: a tool the model must be told about before it uses it is a tool
    # it will not think to use.
    assert CODEMODE_TOOL_NAME in get_all_tools()
    assert CODEMODE_TOOL_NAME in get_default_tools()


def test_sandbox_excludes_codemode_itself():
    # A script that could start a script would nest without limit, and the
    # nested run would have no deadline worth speaking of.
    names = {t.name for t in get_all_tools()[CODEMODE_TOOL_NAME].sandbox_tools()}
    assert CODEMODE_TOOL_NAME not in names
    assert "read" in names


def test_description_lists_the_live_tool_set():
    tool = get_all_tools()[CODEMODE_TOOL_NAME]
    description = get_tool_definitions([tool])[0].description
    # Dynamic, because the tool set changes on reload and a description naming
    # tools that no longer exist is worse than no catalog at all.
    assert "read" in description
    assert "asyncio.gather" in description
    assert "## Workflow" in description


def test_description_explains_each_recovery():
    description = get_all_tools()[CODEMODE_TOOL_NAME].build_description()
    # Each diagnostic kind means a different next move, so each needs its own
    # sentence rather than a generic "the script failed".
    assert "tools.search" in description
    assert "traceback" in description.lower()
    assert "ran too long" in description


def test_constrained_sampling_is_declared_for_openai():
    # The value of grammar-constrained decoding here is that a capable model
    # writes the script as raw text instead of a JSON-escaped string.
    definition = get_tool_definitions([get_all_tools()[CODEMODE_TOOL_NAME]])[0]
    assert definition.constrained_sampling is not None
    assert definition.constrained_sampling.for_provider("openai")


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_a_computed_value(tool: CodemodeTool):
    result = await tool.execute(CodemodeParams(code="return 1 + 1"))
    assert result.success
    assert result.result == "2"


@pytest.mark.asyncio
async def test_text_output_is_prepended_to_the_return_value(tool: CodemodeTool):
    result = await tool.execute(CodemodeParams(code="text('note')\nreturn {'n': 1}"))
    assert result.success
    # `text` is the model-visible channel and the return value is structured;
    # dropping either would lose information the model asked for.
    assert "note" in result.result
    assert '"n": 1' in result.result


@pytest.mark.asyncio
async def test_a_script_calling_a_real_tool(tool: CodemodeTool, echo_tool: EchoTool):
    result = await tool.execute(CodemodeParams(code="return await tools.echo(value='hi')"))
    assert result.success
    assert json.loads(result.result)["echoed"] == "hi"


@pytest.mark.asyncio
async def test_store_persists_across_calls(tool: CodemodeTool):
    first = await tool.execute(CodemodeParams(code="store('k', [1, 2])\nreturn 'set'"))
    assert first.success
    second = await tool.execute(CodemodeParams(code="return load('k')"))
    assert json.loads(second.result) == [1, 2]


@pytest.mark.asyncio
async def test_store_is_not_committed_when_the_script_fails(tool: CodemodeTool):
    await tool.execute(CodemodeParams(code="store('k', 1)\nraise ValueError('x')"))
    assert tool.store == {}


@pytest.mark.asyncio
async def test_clear_store_drops_the_session(tool: CodemodeTool):
    await tool.execute(CodemodeParams(code="store('k', 1)\nreturn None"))
    tool.clear_store()
    assert tool.store == {}


@pytest.mark.asyncio
async def test_timeout_is_reported_as_such(tool: CodemodeTool):
    result = await tool.execute(CodemodeParams(code="while True:\n    pass"))
    assert not result.success
    # The model has to be able to tell "too slow" from "broken", or it retries
    # identical code.
    assert "timeout" in result.result


@pytest.mark.asyncio
async def test_a_blocked_import_is_reported_not_raised(tool: CodemodeTool):
    result = await tool.execute(CodemodeParams(code="import os\nreturn 1"))
    assert not result.success
    assert "sandbox" in result.result


@pytest.mark.asyncio
async def test_options_line_is_honoured_and_clamped(tool: CodemodeTool):
    result = await tool.execute(CodemodeParams(code='# @options: {"timeout_ms": 60000}\nreturn 1'))
    assert result.success
    assert result.result == "1"


@pytest.mark.asyncio
async def test_bad_options_line_is_a_readable_error(tool: CodemodeTool):
    result = await tool.execute(CodemodeParams(code="# @options: nope\nreturn 1"))
    assert not result.success
    assert "options" in result.result.lower()


@pytest.mark.asyncio
async def test_refresh_rebuilds_the_sandbox(tool: CodemodeTool):
    before = tool._get_sandbox()
    tool.refresh()
    assert tool._get_sandbox() is not before


def test_format_call_leads_with_the_first_line(tool: CodemodeTool):
    assert tool.format_call(CodemodeParams(code="rows = await tools.read(p=1)\nreturn rows")) == (
        "script: rows = await tools.read(p=1)"
    )


def test_format_preview_shows_the_whole_script(tool: CodemodeTool):
    # The script is the whole of what it will do, so approving a one-line
    # summary would approve something the user cannot see.
    script = "a = 1\nb = 2\nreturn a"
    assert tool.format_preview(CodemodeParams(code=script)) == script


def test_format_preview_is_capped(tool: CodemodeTool):
    preview = tool.format_preview(CodemodeParams(code="x = 1\n" * 2000))
    assert preview is not None
    assert len(preview) < 5000
    assert "truncated" in preview


# --------------------------------------------------------------------------
# Search, from inside the sandbox
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_finds_a_tool_by_description():
    result = await get_all_tools()[CODEMODE_TOOL_NAME].execute(
        CodemodeParams(
            code=(
                "m = await tools.search(query='read a file')\n"
                "return [x['name'] for x in m['matches']]"
            )
        )
    )
    assert result.success
    assert "read" in result.result


@pytest.mark.asyncio
async def test_search_returns_a_callable_signature():
    # The point of search is to avoid a second lookup, so the signature comes
    # back with the match.
    result = await get_all_tools()[CODEMODE_TOOL_NAME].execute(
        CodemodeParams(
            code="m = await tools.search(query='read a file')\nreturn m['matches'][0]['signature']"
        )
    )
    assert result.success
    assert "def tools." in result.result


@pytest.mark.asyncio
async def test_search_with_an_empty_query_browses():
    result = await get_all_tools()[CODEMODE_TOOL_NAME].execute(
        CodemodeParams(code="m = await tools.search(query='')\nreturn len(m['matches']) > 3")
    )
    assert result.success
    assert "true" in result.result


@pytest.mark.asyncio
async def test_unknown_tool_recovery_points_at_search():
    # The advice has to be followable: if it says "search for it", search has to
    # exist. It does -- that was a real bug, since the instructions named a tool
    # that was never registered.
    result = await get_all_tools()[CODEMODE_TOOL_NAME].execute(
        CodemodeParams(
            code=(
                "try:\n"
                "    await tools.definitely_not_real()\n"
                "except UnknownTool:\n"
                "    return await tools.search(query='read')"
            )
        )
    )
    assert result.success
    assert "matches" in result.result
