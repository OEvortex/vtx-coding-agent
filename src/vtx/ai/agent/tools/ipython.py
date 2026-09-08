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
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from vtx.ai.agent.tools.base import BaseTool
from vtx.ai.agent.tools_manager import get_ipython_manager
from vtx.core.types import ToolResult

if TYPE_CHECKING:
    pass


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


_ipython_block_cls: type | None = None


def _resolve_ipython_block() -> type | None:
    """Lazily load :class:`vtx.tui.ipython_block.IpythonBlock`.

    Done on first access to avoid a hard ``vtx.ai`` -> ``vtx.tui`` import cycle.
    """
    global _ipython_block_cls
    if _ipython_block_cls is not None:
        return _ipython_block_cls
    try:
        from vtx.tui.ipython_block import IpythonBlock
    except Exception:
        _ipython_block_cls = None
        return None
    _ipython_block_cls = IpythonBlock
    return IpythonBlock


class IpythonTool(BaseTool):
    """Execute Python code in a persistent IPython kernel."""

    name = "ipython"
    tool_icon = ">>>"
    params = IpythonParams
    # The REPL runs in a sandboxed subprocess; gating every cell behind a
    # permission prompt in default ``prompt`` mode defeats the RLM workflow,
    # where the model issues many small cells per turn. Destructive actions
    # are still possible inside the cell, but those are the model's
    # responsibility (just like in tool-first mode).
    mutating = False
    description = (
        "Execute Python code in the persistent IPython kernel. "
        "Use this for ALL work in RLM mode: file ops, shell commands, "
        "web search, goals, sub-agents, and custom logic."
    )
    prompt_guidelines = ()

    @property
    def ui_block(self) -> type | None:  # type: ignore[override]
        """Return the prime-agent-style cell widget when RLM mode is active."""
        from vtx.ai.config import config

        if getattr(config, "mode", "tool_first") != "rlm":
            return None
        return _resolve_ipython_block()

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

        # Build context dictionary for context-as-variable in REPL
        from vtx.ai.agent.dispatcher import get_context

        ctx_dict: dict[str, Any] | None = None
        disp_ctx = get_context()
        if disp_ctx is not None:
            session_obj = disp_ctx.session
            messages_data: list[dict[str, Any]] = []
            tokens_data: dict[str, Any] = {}
            if session_obj is not None:
                for msg in session_obj.messages:
                    role = getattr(msg, "role", "unknown")
                    msg_dict: dict[str, Any] = {"role": role}
                    if role == "tool_result":
                        msg_dict["tool_name"] = getattr(msg, "tool_name", "")
                        msg_dict["tool_call_id"] = getattr(msg, "tool_call_id", "")
                        msg_dict["is_error"] = getattr(msg, "is_error", False)
                    content = getattr(msg, "content", None)
                    if isinstance(content, list):
                        msg_dict["content"] = [
                            getattr(p, "text", str(p)) if hasattr(p, "text") else str(p)
                            for p in content
                        ]
                        # If assistant message contains tool calls, serialize them
                        tool_calls = [
                            {
                                "id": getattr(p, "id", ""),
                                "name": getattr(p, "name", ""),
                                "arguments": getattr(p, "arguments", {}),
                            }
                            for p in content
                            if getattr(p, "type", None) == "tool_call"
                        ]
                        if tool_calls:
                            msg_dict["tool_calls"] = tool_calls
                    else:
                        msg_dict["content"] = str(content) if content is not None else ""
                    messages_data.append(msg_dict)
                totals = session_obj.token_totals()
                tokens_data = {
                    "input_tokens": totals.input_tokens,
                    "output_tokens": totals.output_tokens,
                    "context_tokens": totals.context_tokens,
                    "cache_read_tokens": totals.cache_read_tokens,
                    "cache_write_tokens": totals.cache_write_tokens,
                }

            ctx_dict = {
                "session_id": session_id,
                "cwd": disp_ctx.cwd or os.getcwd(),
                "model": disp_ctx.model or "",
                "system_prompt": disp_ctx.system_prompt or "",
                "messages": messages_data,
                "tokens": tokens_data,
            }

        async def _on_output(text: str) -> None:
            if on_output is not None:
                on_output(text)

        output, errored = await manager.execute(
            session_id,
            params.code,
            on_output=_on_output if on_output is not None else None,
            timeout=params.timeout,
            context=ctx_dict,
        )
        # Propagate the kernel's success/failure to the model via ``is_error``
        # so providers that honor the flag (Anthropic) actually mark the tool
        # result as a failure. Also surface a positive acknowledgement for
        # assignment-only cells that produced no stdout/result, so the model
        # doesn't see an empty tool result and conclude the cell didn't run.
        return ToolResult(success=not errored, result=output, ui_summary=output)


__all__ = ["IpythonParams", "IpythonTool"]
