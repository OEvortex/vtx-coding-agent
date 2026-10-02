"""Adapt VTX's own tool surface to the codemode sandbox.

The sandbox speaks JSON data; the harness speaks text. ``ToolResult.result`` is
``str | None`` -- a tool hands the model a rendered string, not a structured
value -- so wiring a built-in tool into codemode means crossing that gap. Doing
that per-caller is how you end up with a tool whose dict silently becomes
``"{'n': 3}"`` and a script that then does string surgery on its own output.

Two facts drive the shape here:

- **Text that happens to be JSON is decoded.** A tool returning
  ``'[{"id": 1}]'`` is the common case, and handing the script a Python
  ``repr`` would be a trap: ``[{'id': 1}]`` parses as a list of one string.
  Only a *successful* JSON decode becomes data.
- **A failed result raises, and its message crosses.** The sandbox wants a
  model-visible message, and ``ui_summary`` is what a user already sees, so that
  is the text that goes to the model rather than a generic "the tool failed".
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


def adapt_tool(tool: BaseTool) -> CodemodeTool:
    """Wrap a built-in ``BaseTool`` as a sandbox tool.

    The returned tool validates arguments through the same pydantic model the
    harness uses, so a call the sandbox admits is a call the harness would also
    accept -- the schema boundary is not advisory.

    Permission handling is the caller's job: this wrapper calls ``execute``
    directly, which skips ``beforeToolCall``/``afterToolCall``. A host that gates
    tools needs to check there, the same way it does for any other direct
    invocation.
    """

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
            result = await tool.execute(params)
        except ToolError:
            raise
        except Exception as exc:
            # Deliberately opaque. An unclassified exception from a host tool can
            # carry paths, credentials, or connection strings, and none of that
            # belongs in the model's context. `detail` keeps it in host logs.
            raise ToolError(f"{tool.name} failed", detail=f"{type(exc).__name__}: {exc}") from exc
        if not result.success:
            # `ui_summary` is what the user already sees for a failure, so it is
            # the most likely-to-be-actionable text available.
            raise ToolError(result.ui_summary or result.result or f"{tool.name} failed")
        return _decode_result(result.result)

    return CodemodeTool(
        name=tool.name,
        description=tool.description or "",
        input_schema=base_tool_schema(tool),
        execute=run,
    )


def adapt_tools(
    tools: list[BaseTool], *, predicate: Callable[[BaseTool], bool] | None = None
) -> list[CodemodeTool]:
    """Adapt a list of built-in tools, optionally filtered.

    The filter is the authority boundary: a tool that is not passed here is not
    reachable from inside the sandbox, no matter what the model writes. Default
    is everything.
    """
    selected = tools if predicate is None else [t for t in tools if predicate(t)]
    return [adapt_tool(tool) for tool in selected]
