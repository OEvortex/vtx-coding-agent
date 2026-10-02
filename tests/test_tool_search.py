"""The two discovery routes, and the gate between them.

There are two ways a model finds a tool it was not offered, and they answer
different questions. ``tools.search`` runs inside a script, so a model that
already knows it needs something can find and use it in the same turn.
``tool_search`` runs as an ordinary tool call, so what it finds can change what
the model is *offered* next turn.

The gate matters more than either: a tool the operator configured as needing
approval must not be reachable by a script, because a script has nobody to ask.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from vtx.agent.tools.base import BaseTool
from vtx.agent.tools.tool_search import ToolSearchTool
from vtx.protocol.types import ToolResult


class _Empty(BaseModel):
    pass


def _mcp_tool(name: str, description: str, exposure: str, **schema_extra) -> BaseTool:
    """A stand-in for an ``McpTool``: what matters is its exposure and schema."""

    class Remote(BaseTool):
        async def execute(self, params, cancel_event=None) -> ToolResult:
            return ToolResult(success=True, result="ok")

    tool = Remote()
    tool.name = name
    tool.description = description
    tool.params = _Empty
    tool.mutating = False
    tool.exposure = exposure
    tool.namespace = "mcp__docs"
    return tool


# ---- what is searchable --------------------------------------------------


def test_only_script_reachable_tools_are_searchable():
    search = ToolSearchTool()
    search.tool_source = lambda: [
        _mcp_tool("mcp__docs__search", "Search the docs", "codemode"),
        _mcp_tool("mcp__docs__rare", "Rarely used", "codemode-deferred"),
        _mcp_tool("mcp__docs__on_demand", "Loaded on demand", "deferred"),
        _mcp_tool("mcp__docs__nuke", "Delete everything", "hidden"),
    ]
    assert {t.name for t in search.searchable()} == {
        "mcp__docs__search",
        "mcp__docs__rare",
        "mcp__docs__on_demand",
    }


def test_builtins_are_not_searchable():
    # They are either already declared -- so a result would suggest calling
    # something the model can already call -- or filtered out of the session, in
    # which case advertising them is wrong in the other direction.
    search = ToolSearchTool()

    class Plain(BaseTool):
        async def execute(self, params, cancel_event=None) -> ToolResult:
            return ToolResult(success=True, result="ok")

    plain = Plain()
    plain.name = "read"
    plain.description = "Read a file"
    plain.params = _Empty
    search.tool_source = lambda: [plain]
    assert search.searchable() == []


# ---- ranking -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_query_naming_a_parameter_finds_the_tool_that_has_it():
    # The reason the ranker indexes schema property names and not just names and
    # descriptions: a model that knows a tool takes `after` should find it.
    class PageParams(BaseModel):
        after: str = ""

    search_tool = _mcp_tool("mcp__a__search", "Search the docs", "codemode")
    list_tool = _mcp_tool("mcp__b__list", "List issues", "codemode")
    list_tool.params = PageParams

    search = ToolSearchTool()
    search.tool_source = lambda: [search_tool, list_tool]

    result = await search.execute(_params("after"))
    assert result.success
    # "after" appears in neither name nor description -- only in the schema, so
    # a ranker that indexed names alone would find nothing.
    assert "mcp__b__list" in result.result


@pytest.mark.asyncio
async def test_a_match_is_activated_so_the_next_turn_can_call_it():
    # Without the activate callback the model is told about a tool it still
    # cannot call, and the next attempt fails as an unknown name. Reporting a
    # capability it cannot reach is worse than staying quiet about it.
    search = ToolSearchTool()
    search.tool_source = lambda: [_mcp_tool("mcp__docs__search", "Search the docs", "codemode")]
    activated: list = []
    search.activate = lambda names: activated.extend(names)

    result = await search.execute(_params("search the docs"))
    assert result.success
    assert activated == ["mcp__docs__search"]
    assert "now in your tool list" in result.result


@pytest.mark.asyncio
async def test_a_match_carries_the_signature_so_finding_and_calling_are_one_step():
    search = ToolSearchTool()
    search.tool_source = lambda: [_mcp_tool("mcp__docs__search", "Search the docs", "codemode")]
    result = await search.execute(_params("search"))
    assert result.success
    # The parameter names, in the same turn the tool was found.
    assert "def tools.mcp__docs__search" in result.result


@pytest.mark.asyncio
async def test_no_match_says_so_and_points_at_the_namespaces():
    search = ToolSearchTool()
    search.tool_source = lambda: [_mcp_tool("mcp__docs__search", "Search the docs", "codemode")]
    result = await search.execute(_params("zzzz unrelated"))
    assert result.success
    assert "Nothing matched" in result.result
    # Naming the namespaces is more useful than a bare failure: the model now
    # knows what there is to search.
    assert "mcp__docs" in result.result


@pytest.mark.asyncio
async def test_nothing_to_find_is_reported_as_such():
    search = ToolSearchTool()
    search.tool_source = lambda: []
    result = await search.execute(_params("anything"))
    assert result.success
    assert "No further tools are available" in result.result


def _params(query: str, limit: int = 8):
    from vtx.agent.tools.tool_search import ToolSearchParams

    return ToolSearchParams(query=query, limit=limit)
