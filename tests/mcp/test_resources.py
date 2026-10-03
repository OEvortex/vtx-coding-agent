"""The three MCP resource tools.

The behaviour worth pinning down here is mostly about what the model is *not*
shown: MCP App user interfaces, per-server bookkeeping fields, and the failure
of one server hiding the resources of every other. A large survey is also the
one payload that can blow past the output cap, so that path is covered.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from vtx.mcp.resources import (
    LIST_MCP_RESOURCE_TEMPLATES_TOOL,
    LIST_MCP_RESOURCES_TOOL,
    READ_MCP_RESOURCE_TOOL,
    McpResourceServer,
    create_mcp_resource_tools,
)

pytestmark = pytest.mark.asyncio


class FakeClient:
    """Stands in for :class:`McpClient`'s resources methods."""

    #: The manager only offers a server whose client is connected.
    connected = True

    def __init__(
        self,
        *,
        resources: list[dict] | None = None,
        templates: list[dict] | None = None,
        contents: dict[str, list[dict]] | None = None,
        pages: dict[str, list[dict]] | None = None,
        fail_list: bool = False,
    ) -> None:
        self.resources = resources if resources is not None else []
        self.templates = templates if templates is not None else []
        self.contents = contents or {}
        self.pages = pages or {}
        self.fail_list = fail_list
        self.reads: list[str] = []
        self.page_calls: list[str | None] = []

    async def list_resources_page(self, cursor=None, options=None):
        self.page_calls.append(cursor)
        items = self.pages.get(cursor or "", self.resources)
        return {"resources": items, "nextCursor": "next" if cursor is None else None}

    async def list_resource_templates_page(self, cursor=None, options=None):
        self.page_calls.append(cursor)
        return {"resourceTemplates": self.templates}

    async def list_resources(self, options=None):
        if self.fail_list:
            raise RuntimeError("listing exploded")
        return [*self.pages.get("", []), *self.resources]

    async def list_resource_templates(self, options=None):
        if self.fail_list:
            raise RuntimeError("listing exploded")
        return self.templates

    async def read_resource(self, uri, options=None):
        self.reads.append(uri)
        if uri not in self.contents:
            raise ValueError(f"no such resource: {uri}")
        return {"contents": self.contents[uri]}


class FakeConfig:
    def __init__(self, name: str, timeout_seconds: int = 30) -> None:
        self.name = name
        self.timeout_seconds = timeout_seconds


class FakeConnection:
    def __init__(self, name: str, client: FakeClient | None, state: str = "connected") -> None:
        self.config = FakeConfig(name)
        self.client = client
        self.status = type("S", (), {"state": state})()


class FakeManager:
    def __init__(self, connections: dict[str, FakeConnection]) -> None:
        self.servers = connections


def _server(name: str, client: FakeClient, kind: str = "resources") -> McpResourceServer:
    """One server, wired for ``kind``.

    The two list tools call different client methods, so a server has to be
    wired for the thing being listed -- wiring one for resources and asking it
    for templates would silently return the wrong list.
    """
    templates = kind == "templates"
    return McpResourceServer(
        name=name,
        timeout_ms=30_000,
        list_page=(
            client.list_resource_templates_page if templates else client.list_resources_page
        ),
        list_all=client.list_resource_templates if templates else client.list_resources,
        read=client.read_resource,
    )


def _tool(name: str, servers: list[McpResourceServer]):

    return _tool_from(name, lambda: servers)


def _tool_from(name: str, servers: Callable[[], list[McpResourceServer]]):
    """Build a tool over an already-callable server source."""
    from vtx.mcp.resources import (
        McpListResourcesTool,
        McpListResourceTemplatesTool,
        McpReadResourceTool,
    )

    return {
        LIST_MCP_RESOURCES_TOOL: McpListResourcesTool,
        LIST_MCP_RESOURCE_TEMPLATES_TOOL: McpListResourceTemplatesTool,
        READ_MCP_RESOURCE_TOOL: McpReadResourceTool,
    }[name](servers)


def _params(tool, **kwargs):
    return tool.params(**kwargs)


# ---- filtering ------------------------------------------------------------


async def test_listing_excludes_mcp_app_resources():
    client = FakeClient(
        resources=[{"uri": "file://a", "name": "a"}, {"uri": "ui://panel", "name": "panel"}]
    )
    tool = _tool(LIST_MCP_RESOURCES_TOOL, [_server("srv", client)])
    result = await tool.execute(_params(tool))
    assert json.loads(result.result)["resources"] == [
        {"server": "srv", "uri": "file://a", "name": "a"}
    ]


# ---- listing --------------------------------------------------------------


async def test_listing_one_server_returns_that_servers_page():
    client = FakeClient(resources=[{"uri": "file://a", "name": "a"}])
    tool = _tool(LIST_MCP_RESOURCES_TOOL, [_server("srv", client)])
    result = await tool.execute(_params(tool, server="srv"))
    payload = json.loads(result.result)
    assert payload["server"] == "srv"
    assert payload["nextCursor"] == "next"
    assert payload["resources"][0]["server"] == "srv"


async def test_a_cursor_pages_within_one_server():
    client = FakeClient(pages={"": [{"uri": "file://a"}], "next": [{"uri": "file://b"}]})
    tool = _tool(LIST_MCP_RESOURCES_TOOL, [_server("srv", client)])
    await tool.execute(_params(tool, server="srv", cursor="next"))
    assert client.page_calls == ["next"]


async def test_a_cursor_without_a_server_is_refused():
    """Silently ignoring it would return a list the model did not ask for."""
    client = FakeClient()
    tool = _tool(LIST_MCP_RESOURCES_TOOL, [_server("srv", client)])
    result = await tool.execute(_params(tool, cursor="next"))
    assert result.success is False
    assert "cursor can only be used when a server is specified" in result.result


async def test_listing_every_server_walks_every_page():
    a = FakeClient(pages={"": [{"uri": "a1"}], "p2": [{"uri": "a2"}]}, resources=[])
    b = FakeClient(resources=[{"uri": "b1"}])
    tool = _tool(LIST_MCP_RESOURCES_TOOL, [_server("b", b), _server("a", a)])
    result = await tool.execute(_params(tool))
    uris = sorted(r["uri"] for r in json.loads(result.result)["resources"])
    assert uris == ["a1", "b1"]


async def test_listing_every_server_is_sorted_for_stability():
    a = FakeClient(resources=[{"uri": "a"}])
    b = FakeClient(resources=[{"uri": "b"}])
    c = FakeClient(resources=[{"uri": "c"}])
    tool = _tool(LIST_MCP_RESOURCES_TOOL, [_server("c", c), _server("a", a), _server("b", b)])
    result = await tool.execute(_params(tool))
    assert [r["server"] for r in json.loads(result.result)["resources"]] == ["a", "b", "c"]


async def test_one_broken_server_does_not_hide_the_others():
    """The point of collecting errors instead of raising."""
    good = FakeClient(resources=[{"uri": "file://a", "name": "a"}])
    bad = FakeClient(fail_list=True)
    tool = _tool(LIST_MCP_RESOURCES_TOOL, [_server("good", good), _server("bad", bad)])
    result = await tool.execute(_params(tool))
    assert result.success is True
    payload = json.loads(result.result)
    assert [r["uri"] for r in payload["resources"]] == ["file://a"]
    assert payload["errors"] == [{"server": "bad", "error": "listing exploded"}]


async def test_listing_with_no_servers_is_an_empty_list():
    tool = _tool(LIST_MCP_RESOURCES_TOOL, [])
    result = await tool.execute(_params(tool))
    assert result.success is True
    assert json.loads(result.result) == {"resources": []}


async def test_an_unknown_server_name_lists_what_is_available():
    tool = _tool(LIST_MCP_RESOURCES_TOOL, [_server("alpha", FakeClient())])
    result = await tool.execute(_params(tool, server="beta"))
    assert result.success is False
    assert 'MCP server "beta" has no resources' in result.result
    assert "Servers with resources: alpha" in result.result


async def test_an_unknown_server_name_with_nothing_connected():
    tool = _tool(LIST_MCP_RESOURCES_TOOL, [])
    result = await tool.execute(_params(tool, server="beta"))
    assert result.success is False
    assert "has no resources" in result.result


async def test_a_large_listing_is_truncated_to_a_file():
    """A survey of every server is the payload that can get large."""
    client = FakeClient(resources=[{"uri": f"file://{i}", "name": "x" * 200} for i in range(500)])
    tool = _tool(LIST_MCP_RESOURCES_TOOL, [_server("srv", client)])
    result = await tool.execute(_params(tool))
    assert "elided" in result.result
    assert "Full listing:" in result.result


async def test_listing_templates_asks_for_templates():
    client = FakeClient(templates=[{"uriTemplate": "file://{path}", "name": "t"}])
    tool = _tool(LIST_MCP_RESOURCE_TEMPLATES_TOOL, [_server("srv", client, "templates")])
    payload = json.loads((await tool.execute(_params(tool))).result)
    assert payload["resourceTemplates"][0]["uriTemplate"] == "file://{path}"


# ---- reading --------------------------------------------------------------


async def test_reading_a_text_resource_returns_its_text():
    client = FakeClient(
        contents={"file://a": [{"uri": "file://a", "mimeType": "text/plain", "text": "hello"}]}
    )
    tool = _tool(READ_MCP_RESOURCE_TOOL, [_server("srv", client)])
    result = await tool.execute(_params(tool, server="srv", uri="file://a"))
    assert result.success is True
    assert "hello" in result.result
    assert client.reads == ["file://a"]


async def test_reading_a_binary_resource_names_a_file():
    """A model cannot consume bytes, but it can read the file they landed in."""
    import base64

    blob = base64.b64encode(b"\x00\x01\x02binary").decode()
    client = FakeClient(
        contents={
            "file://b": [{"uri": "file://b", "mimeType": "application/octet-stream", "blob": blob}]
        }
    )
    tool = _tool(READ_MCP_RESOURCE_TOOL, [_server("srv", client)])
    result = await tool.execute(_params(tool, server="srv", uri="file://b"))
    assert "saved to" in result.result


async def test_reading_several_contents_labels_each_one():
    """Directory-like resources would otherwise be unattributable."""
    client = FakeClient(
        contents={
            "dir://": [
                {"uri": "dir://a", "mimeType": "text/plain", "text": "alpha"},
                {"uri": "dir://b", "mimeType": "text/plain", "text": "beta"},
            ]
        }
    )
    tool = _tool(READ_MCP_RESOURCE_TOOL, [_server("srv", client)])
    result = await tool.execute(_params(tool, server="srv", uri="dir://"))
    assert "dir://a:" in result.result
    assert "dir://b:" in result.result
    assert "alpha" in result.result and "beta" in result.result


async def test_reading_an_empty_resource_says_so():
    client = FakeClient(contents={"file://empty": []})
    tool = _tool(READ_MCP_RESOURCE_TOOL, [_server("srv", client)])
    result = await tool.execute(_params(tool, server="srv", uri="file://empty"))
    assert result.success is True
    assert "is empty" in result.result


async def test_reading_a_missing_resource_reports_the_error():
    client = FakeClient(contents={})
    tool = _tool(READ_MCP_RESOURCE_TOOL, [_server("srv", client)])
    result = await tool.execute(_params(tool, server="srv", uri="file://nope"))
    assert result.success is False
    assert "no such resource" in result.result


async def test_reading_requires_both_arguments():
    """The schema requires both, so this is the guard against a bypass."""
    from pydantic import ValidationError

    client = FakeClient()
    tool = _tool(READ_MCP_RESOURCE_TOOL, [_server("srv", client)])
    with pytest.raises(ValidationError):
        tool.params(uri="file://a")


# ---- tool properties ------------------------------------------------------


async def test_the_tools_are_read_only():
    """Reading cannot change anything, so it must not prompt for approval."""
    servers = [_server("srv", FakeClient())]
    for name in (
        LIST_MCP_RESOURCES_TOOL,
        LIST_MCP_RESOURCE_TEMPLATES_TOOL,
        READ_MCP_RESOURCE_TOOL,
    ):
        assert _tool(name, servers).mutating is False


async def test_the_tool_names_are_the_ones_models_already_know():
    tools = create_mcp_resource_tools(FakeManager({"srv": FakeConnection("srv", FakeClient())}))
    assert [t.name for t in tools] == [
        LIST_MCP_RESOURCES_TOOL,
        LIST_MCP_RESOURCE_TEMPLATES_TOOL,
        READ_MCP_RESOURCE_TOOL,
    ]


async def test_no_tools_without_any_configured_server():
    """Three tools that can only return an empty list are noise."""
    assert create_mcp_resource_tools(FakeManager({})) == []


async def test_a_disconnected_server_is_left_out():
    """Offering a server that cannot answer sends the model to a dead end."""
    manager = FakeManager(
        {
            "up": FakeConnection("up", FakeClient(resources=[{"uri": "u"}])),
            "down": FakeConnection("down", FakeClient(), state="failed"),
        }
    )
    tools = {t.name: t for t in create_mcp_resource_tools(manager)}
    listing = tools[LIST_MCP_RESOURCES_TOOL]
    result = await listing.execute(listing.params())
    payload = json.loads(result.result)
    assert {r["server"] for r in payload["resources"]} == {"up"}


async def test_the_two_list_tools_ask_for_different_things():
    client = FakeClient(
        resources=[{"uri": "file://a", "name": "a"}],
        templates=[{"uriTemplate": "file://{p}", "name": "t"}],
    )
    manager = FakeManager({"srv": FakeConnection("srv", client)})
    tools = {t.name: t for t in create_mcp_resource_tools(manager)}

    resources = tools[LIST_MCP_RESOURCES_TOOL]
    templates = tools[LIST_MCP_RESOURCE_TEMPLATES_TOOL]
    assert json.loads((await resources.execute(resources.params())).result)["resources"]
    assert json.loads((await templates.execute(templates.params())).result)["resourceTemplates"]


async def test_the_server_list_is_read_at_call_time():
    """A session's servers come and go; a captured list would go stale."""
    holders: list[list[McpResourceServer]] = [[]]
    tool = _tool_from(LIST_MCP_RESOURCES_TOOL, lambda: holders[0])
    assert json.loads((await tool.execute(tool.params())).result) == {"resources": []}

    client = FakeClient(resources=[{"uri": "file://a", "name": "a"}])
    holders[0] = [_server("late", client)]
    assert json.loads((await tool.execute(tool.params())).result)["resources"] == [
        {"server": "late", "uri": "file://a", "name": "a"}
    ]


async def test_format_call_is_readable():
    tool = _tool(READ_MCP_RESOURCE_TOOL, [])
    assert tool.format_call(tool.params(server="srv", uri="file://a")) == (
        "read_mcp_resource server=srv uri=file://a"
    )
    listing = _tool(LIST_MCP_RESOURCES_TOOL, [])
    assert listing.format_call(listing.params()) == "list_mcp_resources server=all"
