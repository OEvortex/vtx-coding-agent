"""Bridge between the hook system and the extension EventBus.

Loads hook YAML configs (``.vtx/hooks.yml``) and registers handlers on the
:class:`~vtx.extensions.EventBus` so that hook events fire alongside
extension events without either system knowing about the other.

Event mapping (extension EventBus → hook system):

    session_start  → SessionStart
    session_end    → SessionEnd
    turn_start     → TurnStart
    turn_end       → TurnEnd
    tool_call      → PreToolUse   (blocking)
    tool_result    → PostToolUse  (blocking: output rewrite)
    compaction_start → PreCompact
    compaction_end   → PostCompact
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from .loader import load_hooks
from .registry import HookRegistry
from .runtime import HookContextBuilder, HookRuntime
from .types import HookConfig, HookResult

log = logging.getLogger("vtx.hooks")

# Extension EventBus event name → list of hook event names it maps to.
_EVENT_MAP: dict[str, list[str]] = {
    "session_start": ["SessionStart"],
    "session_end": ["SessionEnd"],
    "turn_start": ["TurnStart"],
    "turn_end": ["TurnEnd"],
    "tool_call": ["PreToolUse"],
    "tool_result": ["PostToolUse"],
    "compaction_start": ["PreCompact"],
    "compaction_end": ["PostCompact"],
}

# Events where the EventBus expects ``block``/``reason`` semantics from the
# handler return value.
_BLOCKING_EXT_EVENTS: frozenset[str] = frozenset({"tool_call", "tool_result"})


class HookBridge:
    """Connects the hook system to an extension :class:`EventBus`.

    Usage::

        bridge = HookBridge(bus, project_path=Path(".vtx/hooks.yml"))
        await bridge.load()
        # … hooks are now active …
        await bridge.unload()
    """

    def __init__(
        self, bus: Any, *, project_path: Path | None = None, global_path: Path | None = None
    ) -> None:
        self._bus = bus
        self._project_path = project_path
        self._global_path = global_path
        self.registry = HookRegistry()
        self.runtime = HookRuntime(self.registry)
        self._registered_handlers: dict[str, Any] = {}

    async def load(self) -> None:
        """Load hook configs from YAML and register EventBus handlers."""
        snapshot = await load_hooks(
            self.registry, project_path=self._project_path, local_path=self._global_path
        )
        if not snapshot.events:
            return

        # Register each hook from the snapshot into the registry so the
        # runtime can dispatch it.  Command-type hooks get a shell executor
        # handler; others get a no-op handler (informational only).
        for hook_event, hook_configs in snapshot.events.items():
            for cfg in hook_configs:
                handler = _make_handler_for_config(cfg)
                await self.registry.register(hook_event, cfg, handler=handler)

        total = sum(len(hooks) for hooks in snapshot.events.values())
        log.debug("HookBridge loaded %d hook(s) from config", total)

        # Wire EventBus events → hook runtime dispatch.  Register
        # unconditionally for any event present in the snapshot; the
        # runtime will no-op when no hooks match.
        registered_hook_events: set[str] = set()
        for ext_event, hook_events in _EVENT_MAP.items():
            for hook_event in hook_events:
                if hook_event in snapshot.events and hook_event not in registered_hook_events:
                    self._register_handler(ext_event, hook_event)
                    registered_hook_events.add(hook_event)

    def _register_handler(self, ext_event: str, hook_event: str) -> None:
        """Register a single EventBus handler for *ext_event*."""
        bridge = self

        async def _handler(event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
            return await bridge._dispatch(hook_event, ext_event, payload)

        key = f"{ext_event}:{hook_event}"
        self._bus.on(ext_event, _handler)
        self._registered_handlers[key] = _handler

    async def _dispatch(
        self, hook_event: str, ext_event: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch a hook event, running all matching handlers."""
        tool_name = payload.get("tool_name")
        ctx = self._build_context(hook_event, payload)

        # Claim-once hooks
        once_results, _ = await self.runtime.emit_claim_once(hook_event, ctx, tool_name=tool_name)
        # Regular hooks
        regular_results = await self.runtime.emit(hook_event, ctx, tool_name=tool_name)

        merged: dict[str, Any] = {}
        merged.update(once_results)
        merged.update(regular_results)

        # For blocking events the EventBus expects ``block``/``reason`` keys.
        # Translate from the hook system's ``blocking_error`` convention.
        if ext_event in _BLOCKING_EXT_EVENTS:
            self._translate_blocking(merged)
        return merged

    @staticmethod
    def _translate_blocking(result: dict[str, Any]) -> None:
        """In-place translate ``blocking_error`` → ``block``/``reason``."""
        for r in result.get("results", []):
            if isinstance(r, HookResult) and r.blocking_error:
                result["block"] = True
                result["reason"] = r.blocking_error
                return
        if result.get("blocking_error"):
            result["block"] = True
            result["reason"] = result.pop("blocking_error")

    @staticmethod
    def _build_context(hook_event: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Build a hook context dict from the EventBus payload."""
        builder = HookContextBuilder

        if hook_event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            return builder.for_tool_event(
                hook_event,
                tool_name=payload.get("tool_name", ""),
                arguments=payload.get("args"),
                tool_call_id=payload.get("tool_call_id"),
                result=payload.get("result"),
            )
        if hook_event in ("PermissionRequest", "PermissionDenied"):
            return builder.for_permission_event(
                hook_event,
                permission=payload.get("permission", ""),
                tool_name=payload.get("tool_name"),
                arguments=payload.get("args"),
            )
        if hook_event in ("PreCompact", "PostCompact"):
            return {
                "event": hook_event,
                "session_id": payload.get("session_id"),
                "cwd": payload.get("cwd"),
                "tokens_before": payload.get("tokens_before"),
                "tokens_after": payload.get("tokens_after"),
                "aborted": payload.get("aborted", False),
                "reason": payload.get("reason"),
            }
        return builder.for_session_event(
            hook_event, session_id=payload.get("session_id"), cwd=payload.get("cwd")
        )

    async def unload(self) -> None:
        """Clear all registered handlers and reset the registry."""
        await self.registry.clear()
        self._registered_handlers.clear()


# ---------------------------------------------------------------------------
# Handler factories
# ---------------------------------------------------------------------------


def _make_handler_for_config(cfg: HookConfig) -> Any:
    """Return an async handler callable appropriate for *cfg*."""
    if cfg.type == "command" and cfg.command:
        cmd = cfg.command
        timeout = cfg.timeout

        async def _command_handler(context: dict[str, Any], config: HookConfig) -> HookResult:
            return await run_command_hook(cmd, timeout)

        return _command_handler

    if cfg.type == "prompt":
        text = cfg.prompt_text or ""

        async def _prompt_handler(context: dict[str, Any], config: HookConfig) -> HookResult:
            # A prompt hook doesn't execute anything; it records the
            # instruction so it's visible in hook output/metadata.
            return HookResult(exit_code=0, output=text, metadata={"prompt": text})

        return _prompt_handler

    if cfg.type == "http" and cfg.url:
        url = cfg.url
        timeout = cfg.timeout

        async def _http_handler(context: dict[str, Any], config: HookConfig) -> HookResult:
            return await _run_http_hook(url, timeout, context, cfg)

        return _http_handler

    if cfg.type == "agent":
        instructions = cfg.agent_instructions or ""

        async def _agent_handler(context: dict[str, Any], config: HookConfig) -> HookResult:
            # ponytail: full delegation spawns a sub-agent; for now we surface
            # the instruction as output. Wire to subagent/background spawn when
            # the bridge is given a task runner.
            return HookResult(
                exit_code=0, output=instructions, metadata={"agent_instructions": instructions}
            )

        return _agent_handler

    return None


async def _run_http_hook(
    url: str, timeout: int | None, context: dict[str, Any], cfg: HookConfig
) -> HookResult:
    """POST the hook context to *url* and return the response body as output."""

    effective_timeout = timeout if timeout and timeout > 0 else 30
    # Lazy import keeps the hook subsystem import-light.
    try:
        import httpx
    except ImportError:
        return HookResult(
            exit_code=1, output="", blocking_error="hook type 'http' requires the 'httpx' package"
        )
    try:
        async with httpx.AsyncClient(timeout=effective_timeout) as client:
            resp = await client.post(url, json=context)
        body = resp.text.strip()
        if resp.status_code >= 400:
            return HookResult(
                exit_code=resp.status_code,
                output=body,
                blocking_error=f"hook http {url} returned {resp.status_code}",
            )
        return HookResult(exit_code=resp.status_code, output=body)
    except Exception as exc:  # network/timeout
        return HookResult(exit_code=1, output="", blocking_error=f"hook http failed: {exc}")


# ---------------------------------------------------------------------------
# Shell command executor
# ---------------------------------------------------------------------------


async def run_command_hook(command: str, timeout: int | None = None) -> HookResult:
    """Execute a shell command for a ``command``-type hook."""
    effective_timeout = timeout if timeout and timeout > 0 else 30
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.getcwd(),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=effective_timeout)
        output = (stdout or b"").decode(errors="replace").strip()
        if proc.returncode and proc.returncode != 0:
            err_text = (stderr or b"").decode(errors="replace").strip()
            return HookResult(
                exit_code=proc.returncode, output=output or err_text, blocking_error=err_text
            )
        return HookResult(exit_code=0, output=output)
    except TimeoutError:
        return HookResult(
            exit_code=-1,
            output=f"Hook command timed out after {effective_timeout}s",
            blocking_error=f"Hook timed out: {command}",
        )
    except Exception as exc:
        return HookResult(exit_code=1, output=str(exc), blocking_error=f"Hook error: {exc}")
