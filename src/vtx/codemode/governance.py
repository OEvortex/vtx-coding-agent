"""Make a script's tool calls obey the same rules as the model's own.

This is the fix for a back door. A tool called by a model directly goes through
``beforeToolCall``/``afterToolCall`` extension hooks, the permission gate, the
TUI's call records, and cost accounting. A tool called by a *script* went
straight to ``tool.execute()``, skipping all of it -- which meant a single
approved ``codemode`` call could do anything any exposed tool could, with no
second look and nothing shown to the user.

The reference implementations solve this by routing nested calls back through the
host's own ``executeTool``. That is the whole idea and it is worth copying
exactly: an orchestrator does not get a weaker version of the governance that
applies to it. If a user denied a tool, a script must not reach it; if an
extension rewrites arguments, the rewrite must apply; if a call costs money,
the cost is attributed.

The seam is narrow on purpose. :class:`ToolGovernance` wraps a tool and a
dispatcher; the host supplies the dispatcher. What is guaranteed here is only
what this module can see -- the extension hooks and the permission decision. A
host with a richer pipeline implements the same protocol and gets the rest.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Final

from vtx.codemode.errors import ToolError

if TYPE_CHECKING:
    # Annotations-only (PEP 563); see integration.py.
    from vtx.agent.tools import BaseTool

#: How a host reports a tool call it refused to run. Distinct from a tool that
#: ran and failed, because the model should ask the user rather than retry.
BLOCKED: Final = "blocked"
#: How a host reports a tool that needs a decision it cannot make unattended.
#: A script has no user to prompt, so this is reported rather than auto-approved:
#: silently running a gated call would be the exact failure this module exists
#: to prevent.
UNAPPROVABLE: Final = "unapprovable"


class ToolGovernance:
    """Runs one nested tool call with the host's hooks and permission check.

    Built per tool rather than held as one object because the tool and its
    permission flags travel together, and a wrapper that had to look them up
    would be a place for the two to disagree.
    """

    def __init__(
        self,
        tool: BaseTool,
        *,
        run: Callable[[BaseTool, dict[str, Any]], Awaitable[Any]],
        extensions: Any = None,
        permission: Callable[[BaseTool, dict[str, Any]], Any] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        self._tool = tool
        self._run = run
        self._extensions = extensions
        self._permission = permission
        self._cancel_event = cancel_event

    @property
    def tool(self) -> BaseTool:
        return self._tool

    async def __call__(self, args: dict[str, Any], signal: Any) -> Any:
        from vtx.agent.extensions import TOOL_CALL, TOOL_EXECUTION_END, TOOL_EXECUTION_START

        tool = self._tool
        name = tool.name
        call_id = f"codemode/{name}"

        verdict = self._decide(args)
        if verdict is not None:
            raise ToolError(verdict)

        if self._extensions is not None:
            await self._extensions.emit(
                TOOL_EXECUTION_START,
                cancel_event=self._cancel_event,
                tool_call_id=call_id,
                tool_name=name,
                args=dict(args),
            )
            # A handler may return {"block": True} to refuse, or {"args": {...}}
            # to rewrite. Both are honored: an extension that rewrites arguments
            # for the model must have rewritten them for the script too, or the
            # two paths disagree about what the tool may be handed.
            outcome = await self._extensions.emit(
                TOOL_CALL,
                cancel_event=self._cancel_event,
                tool_call_id=call_id,
                tool_name=name,
                args=dict(args),
                tool=tool,
            )
            if isinstance(outcome, dict):
                if outcome.get("block"):
                    reason = outcome.get("reason") or "blocked by an extension"
                    raise ToolError(f"{name} was not run: {reason}")
                rewritten = outcome.get("args")
                if isinstance(rewritten, dict) and rewritten != args:
                    args = rewritten

        try:
            result = await self._run(tool, args)
        except ToolError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Unclassified exceptions are not forwarded. A host tool's traceback
            # can carry paths and credentials, and none of that belongs in the
            # model's context. `detail` keeps it in host logs.
            await self._announce_end(TOOL_EXECUTION_END, args, False, f"{type(exc).__name__}")
            raise ToolError(f"{name} failed", detail=f"{type(exc).__name__}: {exc}") from exc

        await self._announce_end(
            TOOL_EXECUTION_END, args, bool(getattr(result, "success", True)), None
        )
        return result

    async def _announce_end(
        self, event: str, args: dict[str, Any], success: bool, detail: str | None
    ) -> None:
        """Fire the end-of-call hook. A listener must not be able to fail a call.

        The model's own path lets a ``tool_result`` handler adjust what is
        reported, and that is worth having here too -- but an exception out of a
        handler is swallowed rather than propagated, because an extension's bug
        must not turn a tool that worked into a failure the script then retries.
        """
        if self._extensions is None:
            return
        with contextlib.suppress(Exception):
            await self._extensions.emit(
                event,
                cancel_event=self._cancel_event,
                tool_call_id=f"codemode/{self._tool.name}",
                tool_name=self._tool.name,
                args=dict(args),
                success=success,
                detail=detail,
            )

    def _decide(self, args: dict[str, Any]) -> str | None:
        """Apply the permission gate. Returns a model-facing refusal, or None.

        The gate is a three-way decision and the third case is the one that
        matters here. :func:`check_permission` answers ALLOW or PROMPT, and
        PROMPT means "ask the user" -- which a script has nobody to ask. So
        PROMPT is treated as a refusal, and the model is told to call the tool
        directly so the user can approve it.

        A script is not a way to get a gated call done without asking. It is a
        way to do the ungated ones together.
        """
        if self._permission is None:
            return None
        from vtx.core.permissions import PermissionDecision

        try:
            decision = self._permission(self._tool, args)
        except Exception:
            # A gate that throws must not silently become "allow".
            return (
                f"{self._tool.name} was not run: it could not be permission-checked. "
                "Call it directly so the user can decide."
            )
        if decision is PermissionDecision.ALLOW:
            return None
        return (
            f"{self._tool.name} was not run: it needs approval, and a script has "
            "nobody to ask. Call that tool directly so the user can approve it, "
            "or use a read-only tool that needs no approval."
        )


def governed_invoker(
    tools: list[BaseTool],
    *,
    run: Callable[[BaseTool, dict[str, Any]], Awaitable[Any]],
    extensions: Any = None,
    permission: Callable[[BaseTool, dict[str, Any]], Any] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> Callable[[BaseTool, dict[str, Any]], Awaitable[Any]]:
    """Wrap every tool in ``tools`` and return a single ``invoke`` for the sandbox.

    ``run`` is how a host actually executes a tool -- normally
    ``tool.execute(params)``, or whatever dispatch the host uses. Every call
    goes through it, including the fallback for a tool that arrived after the
    wrappers were built, so there is exactly one execution path.

    ``permission`` is the host's gate, usually
    :func:`vtx.core.permissions.check_permission`. Omitting it is the
    pre-governance behavior, kept because a host with no gate at all should not
    have to invent one to use the sandbox.
    """
    governed = {
        tool.name: ToolGovernance(
            tool, run=run, extensions=extensions, permission=permission, cancel_event=cancel_event
        )
        for tool in tools
    }

    async def invoke(tool: BaseTool, args: dict[str, Any]) -> Any:
        entry = governed.get(tool.name)
        if entry is None:
            return await run(tool, args)
        return await entry(args, None)

    return invoke
