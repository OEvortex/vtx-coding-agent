"""The exposure taxonomy, checked at the layer where it actually bites.

``tests/mcp/test_exposure.py`` covers the resolution rules in isolation. What
these check is that the *runtime* honors them, which is a different and more
consequential claim: an exposure that resolves correctly but leaves a tool
declared to the model anyway has saved nothing.

Five questions per exposure, and each is a way the feature could be inert:

1. is it declared as a tool call the model may invoke by name?
2. is it in the session's pool at all?
3. may a script call it?
4. does the codemode catalog list it?
5. can ``tool_search`` find it?
"""

from __future__ import annotations

from typing import Any, NamedTuple

import pytest
from pydantic import BaseModel

from vtx.agent.extensions import EventBus
from vtx.agent.runtime import ConversationRuntime
from vtx.agent.tools import get_all_tools
from vtx.agent.tools.base import BaseTool
from vtx.protocol.types import ToolResult

pytestmark = pytest.mark.asyncio

#: What each exposure is supposed to mean, as a row. Written out rather than
#: computed from the implementation, so a change to the rules that also changes
#: this table has to be a deliberate edit rather than a tautology.
EXPECTED = {
    #             declared  in_pool  script  catalog  searchable
    "direct": (True, True, True, False, False),
    "codemode": (False, True, True, True, True),
    "codemode-deferred": (False, True, True, False, True),
    "deferred": (False, True, True, False, True),
    "hidden": (False, False, False, False, False),
}


class Reach(NamedTuple):
    declared: bool
    in_pool: bool
    in_script: bool
    in_catalog: bool
    searchable: bool


def _mcp_tool(name: str, exposure: str, *, description: str = "A remote tool") -> BaseTool:
    class Remote(BaseTool):
        async def execute(self, params, cancel_event=None) -> ToolResult:
            return ToolResult(success=True, result="ran")

    tool = Remote()
    tool.name = name
    tool.description = description
    tool.mutating = False
    tool.exposure = exposure
    tool.namespace = "mcp__docs"
    tool.params = type("P", (BaseModel,), {})
    return tool


async def _reach(name: str, exposure: str, **kw: Any) -> Reach:
    runtime = ConversationRuntime(cwd=".", model="gpt-test", tools=[], extensions=EventBus())
    try:
        runtime._mcp_tools = [_mcp_tool(name, exposure, **kw)]
        runtime._sync_mcp_tools()
        codemode = get_all_tools()["codemode"]
        return Reach(
            declared=name in [t.name for t in runtime.declared_tools()],
            in_pool=name in [t.name for t in runtime.tools],
            in_script=name in {t.name for t in codemode.session_tools()},
            in_catalog=name in codemode.build_description(),
            searchable=name in {t.name for t in get_all_tools()["tool_search"].searchable()},
        )
    finally:
        await runtime.close()


@pytest.mark.parametrize("exposure", sorted(EXPECTED))
async def test_each_exposure_means_what_the_table_says(exposure):
    reach = await _reach("mcp__docs__search", exposure)
    assert tuple(reach) == EXPECTED[exposure], reach


async def test_a_codemode_tool_is_not_declared_to_the_model():
    # The load-bearing assertion. If this fails, the entire taxonomy saves
    # nothing: a two-hundred-tool server would still cost two hundred tool
    # definitions on every turn, which is the cost the setting exists to avoid.
    reach = await _reach("mcp__docs__search", "codemode")
    assert reach.declared is False
    # ...while remaining fully reachable, which is what makes hiding it safe.
    assert reach.in_pool and reach.in_script and reach.in_catalog


async def test_a_hidden_tool_is_reachable_by_nobody_at_all():
    # Not merely unlisted. A tool that could still be called by name from inside
    # a script is not hidden, it is just unadvertised -- and a model that reads
    # its own declarations would find it.
    reach = await _reach("mcp__docs__nuke", "hidden")
    assert reach == Reach(False, False, False, False, False)


async def test_a_direct_tool_is_still_composable_in_a_script():
    # A model that needs six of a server's tools in one turn should not have to
    # pay six. Declared directly *and* callable from a script is not a conflict;
    # it is the cheaper way to get the same work done.
    reach = await _reach("mcp__docs__search", "direct")
    assert reach.declared and reach.in_script
    # Not re-listed in the codemode catalog, though: the model already has it in
    # front of it, so advertising it again would be description for description.
    assert not reach.in_catalog


async def test_a_search_cannot_promote_a_tool_the_profile_denied():
    # The regression this pins: the activation lookup used to widen to every
    # registered tool, so asking for a denied one by description un-denied it.
    runtime = ConversationRuntime(cwd=".", model="gpt-test", tools=[], extensions=EventBus())
    try:
        runtime._mcp_tools = [
            _mcp_tool("mcp__docs__nuke", "codemode", description="Delete the docs corpus")
        ]
        runtime._sync_mcp_tools()
        # An operator profile that denies it by name.
        runtime.tools = [t for t in runtime.tools if t.name != "mcp__docs__nuke"]
        runtime._wire_codemode()

        from vtx.agent.tools.tool_search import ToolSearchParams

        search = get_all_tools()["tool_search"]
        result = await search.execute(ToolSearchParams(query="delete the docs corpus"))

        assert "nuke" not in (result.result or "")
        assert "mcp__docs__nuke" not in [t.name for t in runtime.tools]
        assert "mcp__docs__nuke" not in [t.name for t in runtime.declared_tools()]
    finally:
        await runtime.close()


async def test_a_search_does_promote_a_permitted_deferred_tool():
    # The other half. Restricting activation to the live set is only correct
    # because the live set is what a search already draws from -- if that
    # changed, this would break.
    runtime = ConversationRuntime(cwd=".", model="gpt-test", tools=[], extensions=EventBus())
    try:
        runtime._mcp_tools = [
            _mcp_tool("mcp__docs__reindex", "deferred", description="Rebuild the search index")
        ]
        runtime._sync_mcp_tools()
        before = [t.name for t in runtime.declared_tools()]

        from vtx.agent.tools.tool_search import ToolSearchParams

        search = get_all_tools()["tool_search"]
        result = await search.execute(ToolSearchParams(query="rebuild the search index"))

        assert "mcp__docs__reindex" in (result.result or "")
        assert "now in your tool list" in result.result
        assert "mcp__docs__reindex" not in before
    finally:
        await runtime.close()


async def test_builtins_are_still_declared_and_callable():
    # Exposure is an MCP concept. A built-in with no exposure must be entirely
    # unaffected by any of this -- it is declared to the model *and* callable
    # from a script, exactly as before.
    from vtx.agent.tools import get_default_tools, get_tools_with_extensions

    runtime = ConversationRuntime(
        cwd=".",
        model="gpt-test",
        tools=get_tools_with_extensions(get_default_tools()),
        extensions=EventBus(),
    )
    try:
        declared = {t.name for t in runtime.declared_tools()}
        codemode = get_all_tools()["codemode"]
        scripted = {t.name for t in codemode.session_tools()}
        assert "read" in declared
        assert "read" in scripted
    finally:
        await runtime.close()


async def test_a_hidden_tool_contributes_nothing_to_the_system_prompt():
    """The last way a hidden tool could leak: its prompt guidelines.

    `# Tool usage` is assembled from every tool in the list the prompt is built
    with, so passing the full pool there would print a hidden tool's
    `prompt_guidelines` into the prompt -- telling the model the capability
    exists in prose while every route to it is closed.
    """
    runtime = ConversationRuntime(cwd=".", model="gpt-test", tools=[], extensions=EventBus())
    try:
        hidden = _mcp_tool("mcp__docs__nuke", "hidden", description="Delete the docs corpus")
        hidden.prompt_guidelines = ("Use mcp__docs__nuke to remove stale documentation.",)
        runtime._mcp_tools = [hidden]
        runtime._sync_mcp_tools()

        from vtx.agent.prompts import build_system_prompt

        with_hidden = build_system_prompt(".", tools=runtime.tools, context=None)
        assert "mcp__docs__nuke" not in with_hidden

        # Sanity: it *would* have appeared, so this is not a vacuous pass.
        visible = _mcp_tool("mcp__docs__ok", "direct", description="A visible tool")
        visible.prompt_guidelines = ("Use mcp__docs__ok to check the docs.",)
        with_visible = build_system_prompt(".", tools=[visible], context=None)
        assert "mcp__docs__ok" in with_visible
    finally:
        await runtime.close()
