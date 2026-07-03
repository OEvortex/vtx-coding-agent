"""Runtime hook bridge."""

from __future__ import annotations

import logging
from typing import Any

from .registry import HookRegistry, run_hook_handlers

log = logging.getLogger("vtx.hooks")


class HookRuntime:
    def __init__(self, registry: HookRegistry) -> None:
        self.registry = registry

    async def emit(
        self,
        event: str,
        context: dict[str, Any],
        *,
        tool_name: str | None = None,
        fail_safe: bool = True,
    ) -> dict[str, Any]:
        hooks = await self.registry.get_hooks(event, tool_name=tool_name)
        if not hooks:
            return {}
        return await run_hook_handlers(hooks, context, fail_safe=fail_safe)

    async def emit_claim_once(
        self,
        event: str,
        context: dict[str, Any],
        *,
        tool_name: str | None = None,
        fail_safe: bool = True,
    ) -> tuple[dict[str, Any], list[Any]]:
        hooks = await self.registry.claim_once_hooks(event, tool_name=tool_name)
        results: dict[str, Any] = {}
        if hooks:
            results = await run_hook_handlers(hooks, context, fail_safe=fail_safe)
        return results, hooks

    async def has_hooks(self, event: str, tool_name: str | None = None) -> bool:
        return await self.registry.has_hooks(event, tool_name=tool_name)


class HookContextBuilder:
    @staticmethod
    def for_permission_event(
        event: str,
        *,
        permission: str,
        tool_name: str | None = None,
        arguments: dict[str, Any] | None = None,
        session_id: str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event": event,
            "permission": permission,
            "tool_name": tool_name,
            "arguments": arguments,
            "session_id": session_id,
            "cwd": cwd,
            "timestamp": _now(),
        }
        return payload

    @staticmethod
    def for_session_event(
        event: str, session_id: str | None, cwd: str | None = None
    ) -> dict[str, Any]:
        return {"event": event, "session_id": session_id, "cwd": cwd, "timestamp": _now()}

    @staticmethod
    def for_tool_event(
        event: str,
        *,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
        session_id: str | None = None,
        cwd: str | None = None,
        result: Any = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event": event,
            "tool_name": tool_name,
            "arguments": arguments,
            "tool_call_id": tool_call_id,
            "session_id": session_id,
            "cwd": cwd,
            "timestamp": _now(),
        }
        if result is not None:
            payload["result"] = result
        return payload

    @staticmethod
    def for_prompt_event(
        event: str, prompt: str, *, session_id: str | None = None, cwd: str | None = None
    ) -> dict[str, Any]:
        return {
            "event": event,
            "prompt": prompt,
            "session_id": session_id,
            "cwd": cwd,
            "timestamp": _now(),
        }

    @staticmethod
    def for_session_setup_event(
        event: str,
        *,
        session_id: str | None,
        cwd: str | None,
        model: str | None = None,
        provider: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "event": event,
            "session_id": session_id,
            "cwd": cwd,
            "model": model,
            "provider": provider,
            "config": config,
            "timestamp": _now(),
        }


def _now() -> float:
    import time

    return time.time()
