"""Hook configuration loading and snapshot management."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .registry import HookRegistry
from .types import HOOK_EVENTS, HookConfig, HookSnapshot, HookSource

log = logging.getLogger("vtx.hooks")


@dataclass
class HookValidationError:
    event: str
    index: int
    field: str
    message: str
    severity: str = "error"


@dataclass
class _LoadedSource:
    name: str
    source: HookSource
    hooks: dict[str, list[HookConfig]] = field(default_factory=dict)


def validate_hook_configs(hooks: dict[str, list[HookConfig]]) -> list[HookValidationError]:
    errors: list[HookValidationError] = []
    for event, hook_list in hooks.items():
        if event not in HOOK_EVENTS:
            errors.append(HookValidationError(event, -1, "event", f"Unknown hook event: {event}"))
            continue
        if not isinstance(hook_list, list):
            errors.append(HookValidationError(event, -1, "hooks", "Hook list must be an array"))
            continue
        for index, hook in enumerate(hook_list):
            if not isinstance(hook, HookConfig):
                errors.append(
                    HookValidationError(event, index, "hook", "Hook entry must be an object")
                )
                continue
            if hook.type not in {"command", "prompt", "http", "agent"}:
                errors.append(HookValidationError(event, index, "type", "Unsupported hook type"))
            if hook.type == "command" and not hook.command:
                errors.append(
                    HookValidationError(event, index, "command", "command hooks require command")
                )
            if hook.type == "http" and not getattr(hook, "url", ""):
                errors.append(HookValidationError(event, index, "url", "http hooks require url"))
    return errors


def _normalize_source(
    hooks: dict[str, list[dict[str, Any]]], source: HookSource, *, path: Path
) -> _LoadedSource:
    events: dict[str, list[HookConfig]] = {}
    for _yaml_key, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            event = entry.get("event")
            if not isinstance(event, str) or event not in HOOK_EVENTS:
                continue
            events.setdefault(event, []).append(
                HookConfig(
                    event=event,
                    matcher=entry.get("matcher"),
                    type=entry.get("type", "command"),
                    command=entry.get("command", ""),
                    timeout=entry.get("timeout"),
                    once=bool(entry.get("once", False)),
                    prompt_text=entry.get("prompt_text"),
                    agent_instructions=entry.get("agent_instructions"),
                    if_condition=entry.get("if") or entry.get("if_condition"),
                    source=source,
                )
            )
    return _LoadedSource(name=str(path), source=source, hooks=events)


def _read_hooks_yaml(path: Path) -> dict[str, list[dict[str, Any]]]:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


async def load_hooks(
    registry: HookRegistry,
    *,
    project_path: Path | None = None,
    local_path: Path | None = None,
    settings_hooks: dict[str, list[HookConfig]] | None = None,
) -> HookSnapshot:
    await registry.clear()
    sources: list[_LoadedSource] = []
    for label, path, source in [
        ("project", project_path, "project"),
        ("local", local_path, "local"),
    ]:
        if path and path.exists():
            try:
                sources.append(_normalize_source(_read_hooks_yaml(path), source, path=path))
            except Exception as exc:
                log.debug("%s hook load skipped: %s", label, exc)
    if settings_hooks:
        events: dict[str, list[HookConfig]] = {}
        for event, entries in settings_hooks.items():
            if not isinstance(event, str) or event not in HOOK_EVENTS:
                continue
            if not isinstance(entries, list):
                continue
            parsed: list[HookConfig] = []
            for hook in entries:
                parsed.append(
                    HookConfig(
                        event=event,
                        matcher=getattr(hook, "matcher", None),
                        type=getattr(hook, "type", "command") or "command",
                        command=getattr(hook, "command", "") or "",
                        timeout=getattr(hook, "timeout", None),
                        once=bool(getattr(hook, "once", False)),
                        prompt_text=getattr(hook, "prompt_text", None),
                        agent_instructions=getattr(hook, "agent_instructions", None),
                        if_condition=getattr(hook, "if_condition", None),
                        source="user",
                    )
                )
            events[event] = parsed
        sources.append(_LoadedSource(name="settings", source="user", hooks=events))
    merged: dict[str, list[HookConfig]] = {}
    for source in sources:
        for event, hooks in source.hooks.items():
            merged[event] = [*hooks, *merged.get(event, [])]
    for event, hooks in merged.items():
        for hook in hooks:
            await registry.register(event, hook)
    return HookSnapshot(events=merged)


class HookConfigManager:
    def __init__(
        self,
        registry: HookRegistry,
        *,
        project_path: Path | None = None,
        local_path: Path | None = None,
    ) -> None:
        self.registry = registry
        self.project_path = project_path
        self.local_path = local_path
        self._snapshot: HookSnapshot | None = None

    @property
    def snapshot(self) -> HookSnapshot | None:
        return copy.deepcopy(self._snapshot)

    async def load(
        self, settings_hooks: dict[str, list[HookConfig]] | None = None
    ) -> HookSnapshot:
        self._snapshot = await load_hooks(
            self.registry,
            project_path=self.project_path,
            local_path=self.local_path,
            settings_hooks=settings_hooks,
        )
        return copy.deepcopy(self._snapshot)

    async def reload_if_changed(
        self, settings_hooks: dict[str, list[HookConfig]] | None = None
    ) -> HookSnapshot:
        return await self.load(settings_hooks=settings_hooks)

    async def diff_snapshot(self) -> list[str]:
        snapshot = self.snapshot or HookSnapshot()
        runtime = HookSnapshot(
            events={
                event: [entry.config for entry in entries]
                for event, entries in self.registry._entries.items()
                if entries
            }
        )
        return [str(entry) for entry in snapshot.diff(runtime)]
