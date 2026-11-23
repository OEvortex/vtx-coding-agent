"""A tool defined by an explicit JSON schema and an ``(args, ctx)`` callable.

:class:`SchemaTool` is the schema-driven counterpart of the SDK's ``@tool``
decorator: where ``@tool`` derives the parameter model from a function
signature, :class:`SchemaTool` takes the JSON schema and the execute callable
directly. It is the same contract ``ExtensionAPI.register_tool`` uses, so one
callable works in the SDK, in ``Agent(tools=[...])``, and in plugin rows.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ctx.agent.tools.base import BaseTool
from ctx.agent.tools.schema import json_schema_to_pydantic
from ctx.protocol.types import ToolResult

ExecuteFn = Callable[[dict[str, Any], dict[str, Any] | None], Any]
"""The ``(args, ctx)`` callable contract shared by schema-driven tools.

``args`` is the validated parameter dict; ``ctx`` carries ``cancel_event`` and
``cwd``. Returning a string, a dict, or a :class:`ToolResult` all work.
"""


class SchemaTool(BaseTool):
    """A :class:`BaseTool` built from a JSON schema plus an execute callable."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        execute: ExecuteFn,
        mutating: bool = True,
        needs_approval: bool = False,
        tool_icon: str = "→",
        label: str | None = None,
        ui_block: type | None = None,
        params_model: Any = None,
    ) -> None:
        if not name or not isinstance(name, str):
            raise ValueError("Tool name must be a non-empty string")
        self.name = name
        self.description = description
        self.parameters = parameters
        self.params = (
            params_model if params_model is not None else json_schema_to_pydantic(name, parameters)
        )
        self._execute = execute
        self.mutating = mutating
        self.needs_approval = needs_approval
        self.tool_icon = tool_icon
        self.label = label or name
        self._ui_block = ui_block
        self.prompt_guidelines = ()

    async def execute(self, params: Any, cancel_event: Any | None = None) -> ToolResult:
        args = (
            params.model_dump(exclude_none=True) if hasattr(params, "model_dump") else dict(params)
        )
        ctx = {"cancel_event": cancel_event, "cwd": Path.cwd().as_posix()}
        result = self._execute(args, ctx)
        if inspect.isawaitable(result):
            if cancel_event is not None:
                # Race the call against cancellation, so an interrupted tool
                # reports that rather than vanishing.
                task = asyncio.ensure_future(result)
                cancel_task = asyncio.ensure_future(cancel_event.wait())
                try:
                    done, _ = await asyncio.wait(
                        {task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if task in done:
                        result = task.result()
                    else:
                        task.cancel()
                        return ToolResult(success=False, result="Tool execution was interrupted.")
                finally:
                    if not cancel_task.done():
                        cancel_task.cancel()
            else:
                result = await result

        if result is None:
            return ToolResult(success=True, result="(no output)")
        if isinstance(result, ToolResult):
            return result
        if isinstance(result, dict):
            return ToolResult(**result)
        return ToolResult(success=True, result=str(result))

    def format_call(self, params: Any) -> str:
        data = (
            params.model_dump(exclude_none=True) if hasattr(params, "model_dump") else dict(params)
        )
        return " / ".join(f"{k}={v}" for k, v in data.items())

    def format_preview(self, params: Any) -> str | None:
        return None


def define_tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    *,
    execute: ExecuteFn,
    mutating: bool = True,
    needs_approval: bool = False,
    tool_icon: str = "→",
    label: str | None = None,
    ui_block: type | None = None,
) -> SchemaTool:
    """Build a :class:`SchemaTool` from a name, description, JSON schema, and
    an ``execute(args, ctx)`` callable.

    The result is a plain :class:`BaseTool`, so it works everywhere a tool
    does: ``Agent(tools=[...])``, ``ctx.tools.register(...)``,
    ``api.register_tool(...)``, and ``ctx.sdk.register_tool(...)``.
    """
    return SchemaTool(
        name=name,
        description=description,
        parameters=parameters,
        execute=execute,
        mutating=mutating,
        needs_approval=needs_approval,
        tool_icon=tool_icon,
        label=label,
        ui_block=ui_block,
    )


__all__ = ["ExecuteFn", "SchemaTool", "define_tool"]
