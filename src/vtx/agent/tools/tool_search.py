"""Find a tool by what it does, and load it for the rest of the session.

A connected MCP server can offer two hundred tools. Declaring all of them to the
model on every turn is unaffordable, so most are not declared -- they are
reachable, but the model has to know they exist before it can call one. This
tool is how it finds out.

It exists alongside the sandbox's in-script ``tools.search`` rather than
instead of it, because the two answer different questions. ``tools.search``
runs *inside* a script, so a model that already knows it needs something can
find and use it in the same turn, for the price of one. This one runs as an
ordinary tool call, so its results can change what the model is *offered* on the
next turn: a ``deferred`` tool is not declared to the model at all until
something asks for it by name.

That is the difference between "the model can find this if it thinks to look"
and "the model is shown this once it has said what it wants". With a large tool
set, only the second one scales.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vtx.agent.tools.base import BaseTool
from vtx.codemode.declarations import rank
from vtx.codemode.integration import base_tool_schema
from vtx.codemode.types import CodemodeTool
from vtx.protocol.types import ToolResult

TOOL_SEARCH_TOOL_NAME = "tool_search"

DEFAULT_LIMIT = 8

#: How much of a tool's rendered signature goes back in a match. The whole
#: signature, because finding a tool and knowing how to call it should be one
#: round trip -- a model that has to search again for the parameter names has
#: spent the turn it was trying to save.
_MAX_SIGNATURE_CHARS = 2000


class ToolSearchParams(BaseModel):
    query: str = Field(description="What the tool should do, in words.")
    limit: int = Field(
        default=DEFAULT_LIMIT, ge=1, le=50, description="Maximum matches to return."
    )


class ToolSearchTool(BaseTool):
    """Search the tools this session can reach but has not been offered."""

    name = TOOL_SEARCH_TOOL_NAME
    description = (
        "Find tools that are available but not listed, by what they do. Use it "
        "when a capability you need is missing from your tool list -- a connected "
        "MCP server's tools are often reachable without being declared."
    )
    params = ToolSearchParams
    mutating = False
    needs_approval = False
    tool_icon = "⌕"
    prompt_guidelines = (
        "`tool_search` finds tools that exist but are not in your tool list, such "
        "as a connected MCP server's. Use it before reporting a capability as "
        "unavailable.",
    )

    #: A capable model should write the query as raw prose rather than as a
    #: JSON-escaped string.
    constrained_sampling: ClassVar[dict[str, Any]] = {
        "type": "grammar",
        "variants": {
            "openai": r"""
start: json_object
json_object: "{" (pair ("," pair)*)? "}"
pair: string ":" json_value
json_value: string | number | object | array | "true" | "false" | "null"
object: "{" (pair ("," pair)*)? "}"
array: "[" (json_value ("," json_value)*)? "]"
string: /"[^"\\]*(\\.[^"\\]*)*"/
number: /-?\d+/
"""
        },
    }

    def __init__(self) -> None:
        #: ``() -> list[BaseTool]``, the live session tool set. Set by the
        #: runtime; ``None`` falls back to the global registry.
        self.tool_source: Callable[[], Sequence[BaseTool]] | None = None
        #: Called with the names to activate. Set by the runtime, because only it
        #: can change what the next request declares.
        self.activate: Callable[[Sequence[str]], None] | None = None

    def _tools(self) -> list[BaseTool]:
        source = self.tool_source
        if source is not None:
            return list(source())
        from vtx.agent.tools import get_all_tools

        return list(get_all_tools().values())

    def searchable(self) -> list[BaseTool]:
        """The tools worth searching: reachable from a script, not hidden.

        Two exclusions, and both matter.

        A tool with no ``exposure`` is a built-in, and a built-in is either
        already in the model's tool list -- so a search result for it is a
        suggestion to call something it can already call -- or filtered out of
        this session entirely, in which case advertising it would be wrong in
        the other direction. Neither belongs here.

        A tool whose exposure is not script-discoverable is ``hidden``, and
        honoring that only in the search path would make the setting a
        suggestion: a model that searched would find a tool the operator
        configured as unreachable.
        """
        from vtx.mcp.exposure import SCRIPT_DISCOVERABLE

        return [
            tool
            for tool in self._tools()
            if tool.name != self.name and getattr(tool, "exposure", None) in SCRIPT_DISCOVERABLE
        ]

    def format_call(self, params: ToolSearchParams) -> str:
        return f"search: {params.query}"

    async def execute(
        self, params: ToolSearchParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        candidates = self.searchable()
        if not candidates:
            return ToolResult(
                success=True,
                result=(
                    "No further tools are available. Everything this session can "
                    "reach is already in your tool list."
                ),
                ui_summary="tool_search: nothing to find",
            )

        documents = [_as_codemode_tool(tool) for tool in candidates]
        matches = rank(params.query, documents, limit=params.limit)
        if not matches:
            namespaces = sorted({t.namespace for t in documents if t.namespace})
            hint = f" Connected MCP namespaces: {', '.join(namespaces)}." if namespaces else ""
            return ToolResult(
                success=True,
                result=(
                    f"Nothing matched {params.query!r}.{hint} Try different words -- "
                    "a tool's parameters are searched too, so naming a field finds "
                    "the tool that has it."
                ),
                ui_summary="tool_search: no matches",
            )

        found = [match.tool.name for match in matches]
        if self.activate is not None:
            # Load them. Without this the model has been told about a tool it
            # still cannot call, which is worse than not finding it: the next
            # turn would fail on an unknown tool name.
            self.activate(found)

        lines = [
            f"Found {len(found)} tool{'' if len(found) == 1 else 's'}."
            + (" They are now in your tool list." if self.activate is not None else "")
        ]
        for match in matches:
            tool = match.tool
            lines.append(f"\n{tool.identifier()}\n{_signature(tool)}")
        return ToolResult(
            success=True,
            result="\n".join(lines),
            ui_summary=f"tool_search: {', '.join(found[:4])}",
        )


def _as_codemode_tool(tool: BaseTool) -> CodemodeTool:
    """Present a host tool in the shape the ranker indexes.

    Reuses the sandbox adapter's schema so the search text covers a tool's
    parameters and their descriptions. That is what makes a query naming a field
    find the tool that has it -- "cursor" should surface ``list_issues`` when
    ``after: cursor`` is right there in its schema.
    """

    async def unreachable(args: dict[str, Any], signal: Any) -> Any:  # pragma: no cover
        raise RuntimeError("search documents are never executed")

    return CodemodeTool(
        name=tool.name,
        description=tool.description or "",
        input_schema=base_tool_schema(tool),
        output_schema=getattr(tool, "output_schema", None),
        execute=unreachable,
        namespace=getattr(tool, "namespace", None),
    )


def _signature(tool: CodemodeTool) -> str:
    from vtx.codemode.declarations import render_signature

    text = render_signature(tool)
    if len(text) <= _MAX_SIGNATURE_CHARS:
        return text
    return text[:_MAX_SIGNATURE_CHARS] + "\n    # ... signature truncated"


__all__ = ["DEFAULT_LIMIT", "TOOL_SEARCH_TOOL_NAME", "ToolSearchParams", "ToolSearchTool"]
