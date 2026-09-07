"""Persistent IPython REPL tool for RLM mode.

``IpythonTool`` wraps :class:`vtx.ai.agent.ipython_manager.IpythonManager` and exposes
it to the agent as a first-class tool. It streams stdout/stderr back through
``on_output`` so the UI can render incremental output just like any other
tool call.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

from pydantic import BaseModel, Field

from vtx.ai.agent.tools.base import BaseTool
from vtx.ai.agent.tools_manager import get_ipython_manager
from vtx.core.types import ToolResult


class IpythonParams(BaseModel):
    code: str = Field(description="Python code to execute in the persistent IPython kernel.")
    session_id: str | None = Field(
        default=None,
        description="Optional session id. Defaults to the current conversation session.",
    )
    timeout: float = Field(
        default=180.0,
        description="Max execution time in seconds before the snippet is aborted.",
        ge=5.0,
        le=3600.0,
    )


class IpythonTool(BaseTool):
    """Execute Python code in a persistent IPython kernel."""

    name = "ipython"
    tool_icon = ">>>"
    params = IpythonParams
    mutating = True
    description = (
        "Execute Python code in the persistent IPython kernel. "
        "Use this for ALL work in RLM mode: file ops, shell commands, "
        "web search, goals, sub-agents, and custom logic."
    )
    prompt_guidelines = ()

    def format_call(self, params: IpythonParams) -> str:
        session = f" [{params.session_id}]" if params.session_id else ""
        return f"{session}: {params.code}"

    async def execute(
        self,
        params: IpythonParams,
        cancel_event: asyncio.Event | None = None,
        tool_call_id: str | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> ToolResult:
        manager = get_ipython_manager()
        session_id = params.session_id or os.environ.get("VTX_SESSION_ID") or "default"

        async def _on_output(text: str) -> None:
            if on_output is not None:
                on_output(text)

        output = await manager.execute(
            session_id,
            params.code,
            on_output=_on_output if on_output is not None else None,
            timeout=params.timeout,
        )
        return ToolResult(success=True, result=output, ui_summary=output)


__all__ = ["IpythonParams", "IpythonTool"]
