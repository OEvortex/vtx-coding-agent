"""Permission policy — the SDK-side wrapper around Vtx's permission system."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..core.types import ToolResult
from ..tools.base import BaseTool


class PermissionDecision(StrEnum):
    """Outcome of a permission check for a tool call."""

    ALLOW = "allow"
    """Run the tool without further interaction."""

    PROMPT = "prompt"
    """Ask the user / policy owner to approve before running."""


class PermissionPolicy:
    """A pluggable policy that decides whether a tool call is allowed.

    SDK users implement this to plug their own policy in. The default
    policy is :class:`PromptApprove`, which mirrors Vtx's
    :func:`vtx.permissions.check_permission` semantics: read-only tools
    and safe bash commands are auto-approved; mutating tools ask.
    """

    def decide(
        self, tool: BaseTool, arguments: dict[str, Any]
    ) -> PermissionDecision | Awaitable[PermissionDecision]:
        raise NotImplementedError


class AutoApprove(PermissionPolicy):
    """Allow every tool call without prompting. Mirrors ``permissions.mode=auto``."""

    def decide(self, tool: BaseTool, arguments: dict[str, Any]) -> PermissionDecision:
        return PermissionDecision.ALLOW


class AllowlistApprove(PermissionPolicy):
    """Allow tools whose name is in ``allowlist``; prompt for everything else."""

    def __init__(self, allowlist: list[str]) -> None:
        self.allowlist = set(allowlist)

    def decide(self, tool: BaseTool, arguments: dict[str, Any]) -> PermissionDecision:
        if tool.name in self.allowlist:
            return PermissionDecision.ALLOW
        return PermissionDecision.PROMPT


class PromptApprove(PermissionPolicy):
    """Default policy. Mirrors Vtx's :func:`check_permission` semantics.

    Tools with ``mutating=False`` are auto-approved. For mutating tools,
    the policy returns ``PROMPT``. The SDK's permission callback is then
    responsible for collecting a decision from the user / a host app /
    a stub.
    """

    SAFE_BASH: frozenset[str] = frozenset(
        {
            "cat",
            "head",
            "tail",
            "ls",
            "pwd",
            "wc",
            "diff",
            "which",
            "file",
            "stat",
            "du",
            "df",
            "whoami",
            "id",
            "uname",
            "date",
            "realpath",
            "dirname",
            "basename",
        }
    )

    def decide(self, tool: BaseTool, arguments: dict[str, Any]) -> PermissionDecision:
        if not tool.mutating:
            return PermissionDecision.ALLOW
        if tool.name == "bash":
            cmd = str(arguments.get("command", ""))
            head = cmd.strip().split(maxsplit=1)[0] if cmd.strip() else ""
            head = head.rsplit("/", 1)[-1]
            if head in self.SAFE_BASH:
                return PermissionDecision.ALLOW
        return PermissionDecision.PROMPT


@dataclass
class PermissionCallback:
    """Wraps a user-supplied ``decide`` function with synchronous and async support."""

    func: Callable[[BaseTool, dict[str, Any]], Any]

    async def decide_async(self, tool: BaseTool, arguments: dict[str, Any]) -> PermissionDecision:
        import inspect

        result = self.func(tool, arguments)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, PermissionDecision):
            return result
        if isinstance(result, str):
            try:
                return PermissionDecision(result)
            except ValueError:
                pass
        return PermissionDecision.PROMPT


# Re-export ``ToolResult`` for convenience so user code only needs to import
# from vtx.sdk.permissions.
_ = ToolResult
