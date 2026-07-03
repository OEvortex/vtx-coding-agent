"""Hook registry and execution helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .types import HOOK_EVENTS, HookConfig, HookResult, RegisteredHook

log = __import__("logging").getLogger("vtx.hooks")


@dataclass
class _RegistryEntry:
    config: HookConfig
    handler: Callable[..., Any] | None
    handler_id: str
    registration_order: int


def _entry_matches(entry: _RegistryEntry, config: HookConfig) -> bool:
    return (
        entry.config.event == config.event
        and entry.config.type == config.type
        and entry.config.command == config.command
        and entry.config.prompt_text == config.prompt_text
        and entry.config.agent_instructions == config.agent_instructions
        and entry.config.matcher == config.matcher
    )


class HookRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, list[_RegistryEntry]] = {event: [] for event in HOOK_EVENTS}
        self._lock = asyncio.Lock()
        self._counter = 0

    async def register(
        self, event: str, config: HookConfig, handler: Callable[..., Any] | None = None
    ) -> RegisteredHook:
        if event not in self._entries:
            raise ValueError(f"Unknown hook event: {event}")
        async with self._lock:
            self._counter += 1
            entry = _RegistryEntry(
                config=config,
                handler=handler,
                handler_id=f"{event}:{self._counter}",
                registration_order=self._counter,
            )
            self._entries[event].append(entry)
            self._entries[event].sort(key=lambda item: item.registration_order)
            return RegisteredHook(
                config=config,
                handler=handler,
                source=config.source,
                registration_order=self._counter,
            )

    async def register_handler(
        self, event: str, config: HookConfig, handler: Callable[..., Any]
    ) -> RegisteredHook:
        return await self.register(event=event, config=config, handler=handler)

    async def deregister(self, event: str, config: HookConfig) -> bool:
        if event not in self._entries:
            return False
        async with self._lock:
            before = len(self._entries[event])
            self._entries[event] = [
                entry for entry in self._entries[event] if not _entry_matches(entry, config)
            ]
            return len(self._entries[event]) != before

    async def get_hooks(self, event: str, tool_name: str | None = None) -> list[RegisteredHook]:
        if event not in self._entries:
            return []
        async with self._lock:
            entries = list(self._entries[event])
        hooks: list[RegisteredHook] = []
        for entry in entries:
            hook = RegisteredHook(
                config=entry.config,
                handler=entry.handler,
                source=entry.config.source,
                registration_order=entry.registration_order,
            )
            if not hook.matches_tool(tool_name):
                continue
            if not entry.config.enabled or entry.config.once:
                continue
            hooks.append(hook)
        return hooks

    async def has_hooks(self, event: str, tool_name: str | None = None) -> bool:
        return bool(await self.get_hooks(event, tool_name=tool_name))

    async def claim_once_hooks(
        self, event: str, tool_name: str | None = None
    ) -> list[RegisteredHook]:
        if event not in self._entries:
            return []
        async with self._lock:
            claimed: list[RegisteredHook] = []
            remaining: list[_RegistryEntry] = []
            for entry in self._entries[event]:
                if not entry.config.enabled or not entry.config.once:
                    remaining.append(entry)
                    continue
                hook = RegisteredHook(
                    config=entry.config,
                    handler=entry.handler,
                    source=entry.config.source,
                    registration_order=entry.registration_order,
                )
                if not hook.matches_tool(tool_name):
                    remaining.append(entry)
                    continue
                claimed.append(hook)
            self._entries[event] = remaining
            return claimed

    async def clear(self) -> None:
        async with self._lock:
            for event in HOOK_EVENTS:
                self._entries[event] = []
            self._counter = 0


async def run_hook_handlers(
    hooks: list[RegisteredHook], context: dict[str, Any], *, fail_safe: bool = True
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    results: list[HookResult] = []
    for hook in hooks:
        handler = hook.handler
        if handler is None:
            continue
        result = HookResult()
        returned = handler(context, hook.config)
        if asyncio.iscoroutine(returned):
            returned = await returned
        if isinstance(returned, HookResult):
            result = returned
        elif isinstance(returned, dict):
            result = HookResult(
                exit_code=returned.get("exit_code", 0) if "exit_code" in returned else 0,
                duration_ms=returned.get("duration_ms", 0) if "duration_ms" in returned else 0,
                output=returned.get("output") if "output" in returned else None,
                blocking_error=returned.get("blocking_error")
                if "blocking_error" in returned
                else None,
                metadata=returned.get("metadata") if "metadata" in returned else None,
            )
            merged.update(
                {
                    k: v
                    for k, v in returned.items()
                    if k
                    not in {"exit_code", "duration_ms", "output", "blocking_error", "metadata"}
                }
            )
        results.append(result)
    return {"results": results, **merged}
