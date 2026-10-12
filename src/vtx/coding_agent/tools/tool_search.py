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

Its result block follows pi-mono's ``tool_search``: a loaded count, then one
line per match naming the tool and the first line of its description, then the
rendered signature this product keeps, because finding a tool and knowing how
to call it should be one round trip -- a model that has to search again for the
parameter names has spent the turn it was trying to save. ``ui_details`` and
``ui_details_full`` carry the collapsed and expanded body, and ``ui_data``
carries the loaded names, the detail pi-mono attaches to a call as ``details``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from vtx.agent.tools import TOOL_SEARCH_TOOL_NAME
from vtx.agent.tools.base import BaseTool
from vtx.codemode.declarations import rank
from vtx.codemode.integration import base_tool_schema
from vtx.codemode.types import CodemodeTool
from vtx.plugins import mount_tool, plugin
from vtx.protocol.types import ToolResult

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
        "Searches tool metadata with BM25 to find tools that are not loaded yet, "
        "and loads the matches so they are offered on your next call. Use it when "
        "a capability you need is missing from your tool list: a connected MCP "
        "server's tools are often reachable without being declared, and for MCP "
        "tool discovery always use this tool."
    )
    params = ToolSearchParams
    mutating = False
    needs_approval = False
    tool_icon = "⌕"
    prompt_guidelines = (
        "`tool_search` loads tools that are not offered yet. Use it before "
        "reporting a capability as unavailable.",
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
        if not params.query.strip():
            # An empty query matches nothing, and "found nothing" teaches the
            # wrong lesson: the model asked for nothing. pi-mono refuses it
            # rather than ranking against an empty term list.
            msg = "query must not be empty"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

        candidates = self.searchable()
        if not candidates:
            return ToolResult(
                success=True,
                result=(
                    "No further tools are available. Everything this session can "
                    "reach is already in your tool list."
                ),
                ui_summary="[dim]nothing to find[/dim]",
                ui_data=_loaded_data([]),
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
                ui_summary="[dim]no matches[/dim]",
                ui_data=_loaded_data([]),
            )

        found = [match.tool.name for match in matches]
        if self.activate is not None:
            # Load them. Without this the model has been told about a tool it
            # still cannot call -- it would name one that no request offers,
            # which is worse than not finding it at all.
            self.activate(found)

        bullets = [f"- {m.tool.identifier()}: {_first_line(m.tool.description)}" for m in matches]
        sections = [
            f"{m.tool.identifier()}: {_first_line(m.tool.description)}\n{_signature(m.tool)}"
            for m in matches
        ]
        body = "\n\n".join(sections)
        return ToolResult(
            success=True,
            result=f"{_headline(matches)}\n\n{body}",
            ui_summary=f"loaded {len(found)}:{_summary_names(found)}",
            ui_details="\n".join(bullets),
            ui_details_full=body,
            ui_data=_loaded_data(found),
        )


def _headline(matches: Sequence[Any]) -> str:
    """pi-mono's "Loaded N tools. They are available from your next call:"."""
    plural = "" if len(matches) == 1 else "s"
    return f"Loaded {len(matches)} tool{plural}. They are available from your next call:"


def _loaded_data(names: Sequence[str]) -> dict[str, Any]:
    """What pi-mono attaches to a ``tool_search`` call as ``details``."""
    return {"loaded": list(names)}


def _summary_names(found: Sequence[str]) -> str:
    return " " + ", ".join(found[:4]) + ("..." if len(found) > 4 else "")


def _first_line(description: str | None) -> str:
    """The description's opening line, as pi-mono's result lists it."""
    text = (description or "").strip()
    return text.splitlines()[0] if text else ""


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


@plugin(name="tool-search", inject=("tools",))
def apply(ctx, config):
    """Registered but not in the default set: the harness declares it only when
    there is something for it to find.

    A tool declaration is paid on every turn, and this tool's whole purpose is
    finding tools a large MCP server has not offered. A session with no
    script-discoverable tools would pay that for nothing, so it is registered
    (an extension can still override the name, and ``_harness_tool_search``
    finds it) while ``ConversationRuntime.declared_tools`` is what decides
    whether the model is shown it -- the same rule pi-mono uses, activating
    ``tool_search`` for a server with ``deferred`` exposure.
    """
    mount_tool(ctx, ToolSearchTool(), is_default=False, parent_only=False)


PLUGIN = apply
