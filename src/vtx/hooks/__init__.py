"""Vtx hook subsystem."""

from __future__ import annotations

from .agent_hook import AgentHook, AgentHookContext, AgentRunHookContext, CompositeHook
from .bridge import HookBridge, run_command_hook
from .loader import HookConfigManager, validate_hook_configs
from .registry import HookRegistry, run_hook_handlers
from .runtime import HookContextBuilder, HookRuntime
from .types import (
    HOOK_EVENTS,
    HandlerType,
    HookConfig,
    HookDiffEntry,
    HookEvent,
    HookResult,
    HookSnapshot,
    HookSource,
    RegisteredHook,
)

__all__ = [
    "HOOK_EVENTS",
    "AgentHook",
    "AgentHookContext",
    "AgentRunHookContext",
    "CompositeHook",
    "HandlerType",
    "HookBridge",
    "HookConfig",
    "HookConfigManager",
    "HookContextBuilder",
    "HookDiffEntry",
    "HookEvent",
    "HookRegistry",
    "HookResult",
    "HookRuntime",
    "HookSnapshot",
    "HookSource",
    "RegisteredHook",
    "run_command_hook",
    "run_hook_handlers",
    "validate_hook_configs",
]
