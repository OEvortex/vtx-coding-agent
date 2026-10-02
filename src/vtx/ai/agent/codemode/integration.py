"""Adapt VTX's own tool surface to the codemode sandbox.

The sandbox speaks JSON data; the harness speaks text. ``ToolResult.result`` is
``str | None`` -- a tool hands the model a rendered string, not a structured
value -- so wiring a built-in tool into codemode means crossing that gap. Doing
that per-caller is how you end up with a tool whose dict silently becomes
``"{'n': 3}"`` and a script that then does string surgery on its own output.

Three decisions live here, and each is about not throwing information away:

- **A tool that sets ``ToolResult.structured`` keeps it.** That is the escape
  hatch for a tool whose result is data rather than prose -- an MCP
  ``CallToolResult``, most importantly. A script is not a chat model: it can
  read a dict, branch on ``isError``, and index ``content`` without parsing
  anything, and doing that filtering in code is the entire reason to write a
  script. Flattening it first would charge the model for a 20KB result the
  script was about to reduce to three numbers.
- **Text that happens to be JSON is decoded.** A tool returning ``'[{"id": 1}]'``
  is the common case, and handing the script a Python ``repr`` would be a trap:
  ``[{'id': 1}]`` parses as a list of one string. Only a *successful* JSON
  decode becomes data.
- **A failed result raises, unless it carries a structured payload.** The
  sandbox wants a model-visible message, and ``ui_summary`` is what a user
  already sees, so that is the text that crosses. The exception is a structured
  result with ``isError`` set: it arrives as a *value*, because a script that
  can read why a call failed and branch is doing something useful, and raising
  would discard the only text that explains the failure.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from vtx.ai.agent.codemode.errors import InvalidInput, ToolError
from vtx.ai.agent.codemode.types import CodemodeTool
from vtx.ai.agent.tools import BaseTool


def _decode_result(result: Any) -> Any:
    """Turn a ``ToolResult.result`` into JSON data where it can be.

    Falls back to the raw string when it is not JSON. A tool that returns prose
    is normal, and the script should get prose back rather than an error.
    """
    if result is None:
        return None
    if not isinstance(result, str):
        return result
    try:
        return json.loads(result)
    except (TypeError, ValueError):
        return result


def base_tool_schema(tool: BaseTool) -> dict[str, Any]:
    """JSON Schema for a built-in tool's parameters.

    ``extra="forbid"`` and the pydantic title are stripped: the title would
    render into the model-facing signature as noise, and anyOf branches produced
    by optional fields are not something the sandbox validates against anyway.
    """
    schema = tool.params.model_json_schema()
    schema.pop("title", None)
    schema.pop("$defs", None)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for detail in properties.values():
            if isinstance(detail, dict):
                detail.pop("title", None)
    return schema


def base_tool_output_schema(tool: BaseTool) -> dict[str, Any] | None:
    """The shape a script receives, when the tool says so.

    Read from the tool's own ``output_schema`` attribute rather than guessed.
    A tool that declares one is making a claim the catalog will print, and
    guessing instead would print a shape the host does not honor.
    """
    schema = getattr(tool, "output_schema", None)
    return schema if isinstance(schema, dict) else None


def adapt_tool(
    tool: BaseTool,
    *,
    namespace: str | None = None,
    namespace_description: str | None = None,
    listed: bool = True,
    invoke: Callable[[BaseTool, dict[str, Any]], Any] | None = None,
) -> CodemodeTool:
    """Wrap a built-in ``BaseTool`` as a sandbox tool.

    The returned tool validates arguments through the same pydantic model the
    harness uses, so a call the sandbox admits is a call the harness would also
    accept -- the schema boundary is not advisory.

    ``invoke`` replaces the direct ``tool.execute`` call, and is how a host
    routes nested calls back through its own pipeline -- permission gate,
    extension hooks, TUI records, cost accounting. Passing it is what makes a
    script's tool calls governed exactly like the model's own; omitting it is
    what makes them invisible to every gate, so a host that has them should
    pass it. See :mod:`vtx.ai.agent.codemode.governance`.
    """

    # Resolved at wrap time so a per-tool schema is a fixed property of the
    # declaration the model reads, not something re-derived on every call.
    async def run(args: dict[str, Any], signal: Any) -> Any:
        try:
            params = tool.params.model_validate(args)
        except Exception as exc:
            # `invalid_input`, not a plain ToolError: the taxonomy exists so the
            # model picks the right recovery. This one says "fix the arguments",
            # which is a different next move from "the tool declined -- try
            # something else".
            #
            # Only the shape is reported. The pydantic message can be long and
            # names internal model classes, which is not what the model needs in
            # order to fix a call.
            raise InvalidInput(tool.name, detail=str(exc)) from exc

        try:
            if invoke is not None:
                result = await invoke(tool, args)
            else:
                result = await tool.execute(params)
        except ToolError:
            raise
        except Exception as exc:
            # Deliberately opaque. An unclassified exception from a host tool can
            # carry paths, credentials, or connection strings, and none of that
            # belongs in the model's context. `detail` keeps it in host logs.
            raise ToolError(f"{tool.name} failed", detail=f"{type(exc).__name__}: {exc}") from exc

        # Checked before `success`: a structured result with isError set is a
        # value the script can inspect, and raising here would throw away the
        # only explanation of the failure.
        structured = getattr(result, "structured", None)
        if structured is not None:
            return structured
        if not result.success:
            # `ui_summary` is what the user already sees for a failure, so it is
            # the most likely-to-be-actionable text available.
            raise ToolError(result.ui_summary or result.result or f"{tool.name} failed")
        return _decode_result(result.result)

    return CodemodeTool(
        name=tool.name,
        description=tool.description or "",
        input_schema=base_tool_schema(tool),
        output_schema=base_tool_output_schema(tool),
        execute=run,
        namespace=namespace,
        namespace_description=namespace_description,
        listed=listed,
    )


def adapt_tools(
    tools: list[BaseTool],
    *,
    predicate: Callable[[BaseTool], bool] | None = None,
    invoke: Callable[[BaseTool, dict[str, Any]], Any] | None = None,
    listed: Callable[[BaseTool], bool] | None = None,
) -> list[CodemodeTool]:
    """Adapt a list of built-in tools, optionally filtered.

    The filter is the authority boundary: a tool that is not passed here is not
    reachable from inside the sandbox, no matter what the model writes. Default
    is everything.

    ``listed`` decides catalog visibility, which is a *different* boundary from
    reachability and defaults to everything. A tool that is not listed is still
    callable by exact name and still findable through the sandbox's search; it is
    just not advertised. Splitting the two is what lets a hundred MCP tools be
    reachable without a hundred of them costing prompt tokens.
    """
    selected = tools if predicate is None else [t for t in tools if predicate(t)]
    return [
        adapt_tool(
            tool,
            namespace=getattr(tool, "namespace", None),
            namespace_description=getattr(tool, "instructions", None),
            listed=True if listed is None else listed(tool),
            invoke=invoke,
        )
        for tool in selected
    ]
