"""Hook system primitives for vtx."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

HOOK_EVENTS: list[str] = [
    "UserPromptSubmit",
    "SessionStart",
    "SessionEnd",
    "TurnStart",
    "TurnEnd",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "PermissionDenied",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "StopFailure",
    "PreCompact",
    "PostCompact",
    "Notification",
    "PostSampling",
    "Setup",
    "InstructionsLoaded",
    "CwdChanged",
    "FileChanged",
    "WorktreeCreate",
    "WorktreeRemove",
    "ConfigChange",
    "TaskCreated",
    "TaskCompleted",
    "TeammateIdle",
    "Elicitation",
    "ElicitationResult",
]

HookEvent = Literal[
    "UserPromptSubmit",
    "SessionStart",
    "SessionEnd",
    "TurnStart",
    "TurnEnd",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "PermissionDenied",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "StopFailure",
    "PreCompact",
    "PostCompact",
    "Notification",
    "PostSampling",
    "Setup",
    "InstructionsLoaded",
    "CwdChanged",
    "FileChanged",
    "WorktreeCreate",
    "WorktreeRemove",
    "ConfigChange",
    "TaskCreated",
    "TaskCompleted",
    "TeammateIdle",
    "Elicitation",
    "ElicitationResult",
]

HandlerType = Literal["command", "prompt", "http", "agent"]
HookSource = Literal["user", "project", "local", "extension", "runtime"]


@dataclass(frozen=True)
class HookConfig:
    event: HookEvent = "SessionStart"
    matcher: str | None = None
    type: HandlerType = "command"
    command: str = ""
    timeout: int | None = None
    once: bool = False
    prompt_text: str | None = None
    agent_instructions: str | None = None
    if_condition: str | None = None
    source: HookSource = "user"
    enabled: bool = True
    url: str = ""


class HookResult:
    def __init__(
        self,
        *,
        exit_code: int = 0,
        duration_ms: int = 0,
        output: str | None = None,
        blocking_error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.duration_ms = duration_ms
        self.output = output
        self.blocking_error = blocking_error
        self.metadata = metadata or {}


@dataclass
class RegisteredHook:
    config: HookConfig
    handler: Callable[..., Any] | None = None
    source: HookSource = "extension"
    registration_order: int = 0

    def matches_tool(self, tool_name: str | None) -> bool:
        matcher = self.config.matcher
        if matcher is None or tool_name is None:
            return True
        if matcher == tool_name:
            return True
        if matcher.endswith("*"):
            return tool_name.startswith(matcher[:-1])
        if matcher.startswith("*"):
            return tool_name.endswith(matcher[1:])
        return matcher == tool_name


@dataclass
class HookDiffEntry:
    event: str = ""
    kind: str = ""
    source: HookSource = "extension"
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.kind} {self.event} [{self.source}] {self.detail}"


@dataclass
class HookSnapshot:
    events: dict[str, list[HookConfig]] = field(default_factory=dict)

    def diff(self, other: HookSnapshot) -> list[HookDiffEntry]:
        diffs: list[HookDiffEntry] = []
        for event in sorted(set(self.events) | set(other.events)):
            left = self.events.get(event, [])
            right = other.events.get(event, [])
            left_map = {_config_key(hook): hook for hook in left}
            right_map = {_config_key(hook): hook for hook in right}
            for key, hook in right_map.items():
                if key not in left_map:
                    diffs.append(HookDiffEntry(event, "add", hook.source, key))
            for key, hook in left_map.items():
                if key not in right_map:
                    diffs.append(HookDiffEntry(event, "remove", hook.source, key))
        return diffs


def _config_key(hook: HookConfig) -> str:
    parts = [hook.type]
    if hook.type == "command":
        parts.append(hook.command or "")
    elif hook.prompt_text:
        parts.append(hook.prompt_text)
    elif hook.agent_instructions:
        parts.append(hook.agent_instructions)
    else:
        parts.append("")
    if hook.matcher:
        parts.append(hook.matcher)
    return "|".join(parts)
