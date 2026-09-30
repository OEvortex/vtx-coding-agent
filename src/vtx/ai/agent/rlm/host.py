"""Host-side dispatcher for kernel -> host requests (VTX RLM bridge).

Python port of Prime Agent's ``_createKernelHostHandlers`` registry
(``agent-session.ts:10941`` plus the wrapper factories in ``rlm-runtime.ts``
and ``agent-messages.ts``), speaking VTX primitives:

- children spawn through the background sub-agent machinery
  (``vtx.ai.agent.tools.task`` + ``vtx.ai.agent.background``),
- goal/compact/model/catalog state comes from the vtx runtime,
- notices and pending refine/compact requests are queued in
  :mod:`vtx.ai.agent.rlm.registry` and drained by the parent at turn
  boundaries (see that module's docstring for the exact wiring points).

``dispatch_host_request`` never raises: every failure — validation or
internal — degrades to ``{"status": "error", "error": <msg>}``.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from vtx.ai.agent.rlm.registry import (
    ANSWER_PREVIEW_MAX_CHARS,
    ChildNameUnavailable,
    ChildRecord,
    ChildRegistry,
    drain_notices,
    drain_pending_compact,
    drain_pending_refine,
    get_registry,
)

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[Any]]
Handler = Callable[[dict[str, Any], "_Ctx"], Awaitable[Any]]

PREVIEW_MAX_CHARS = 240  # AGENT_OBSERVE_PREVIEW_MAX_CHARS
DEFAULT_FIND_MODELS_LIMIT = 8
MAX_FIND_MODELS_LIMIT = 20
OBSERVE_DEFAULT_LIMIT = 8
OBSERVE_MAX_LIMIT = 50
OBSERVE_DEFAULT_MAX_CHARS = 800
OBSERVE_MIN_MAX_CHARS = 80
OBSERVE_MAX_MAX_CHARS = 2_000
PROGRESS_NOTE_MAX_CHARS = 512  # RLM_PROGRESS_NOTE_MAX_LENGTH (UTF-16 units)
AGENT_MESSAGE_MAX_CHARS = 16_384  # DEFAULT_AGENT_MESSAGE_MAX_CHARS
SAFE_INT_MAX = 2_147_483_647

REFINE_NOTE = (
    "Refinement runs when the current turn ends; applied edits are appended "
    "to your context as a refinement notice and you resume automatically. "
    "Continue working normally."
)
COMPACT_NOTE = (
    "Compaction runs when the current turn ends; you resume automatically "
    "afterwards. Continue working normally."
)
REFINE_NO_TURN_REASON = "no active turn; refine can only be requested while a turn is running"
COMPACT_NO_TURN_REASON = "no active turn; compaction can only be requested while a turn is running"
AGENT_MESSAGE_LIST_AGENTS_REMOVED = (
    "agent_message.list_agents was removed; the family roster now lives in "
    "agent_observe.list_agents(). Restart the Python kernel to load the "
    "current skills, then call await agent_observe.list_agents()."
)
GOALS_DISABLED_MESSAGE = "goals are disabled in this session"
CREATE_SESSION_UNAVAILABLE = "rlm.create_session is not available in this session"


class HostError(Exception):
    """Validation or availability failure whose message is wire-exact."""


@dataclass
class _Ctx:
    session_id: str | None
    tool_executor: ToolExecutor | None
    registry: ChildRegistry


# =================================================================================================
# helpers
# =================================================================================================


def _js_length(text: str) -> int:
    """Measure like JavaScript ``String.length`` (UTF-16 code units)."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _require_str(payload: dict[str, Any], field: str, message: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise HostError(message)
    return value


def _session_messages(ctx: _Ctx) -> list[Any]:
    try:
        from vtx.ai.agent.dispatcher import get_context

        parent_ctx = get_context()
        session = getattr(parent_ctx, "session", None) if parent_ctx else None
        if session is None:
            return []
        return list(session.messages)
    except Exception:
        return []


def _context_window(ctx: _Ctx) -> int | None:
    try:
        from vtx.ai.agent.config import get_harness_config
        from vtx.ai.agent.dispatcher import get_context
        from vtx.ai.models import get_model

        model_info = None
        parent_ctx = get_context()
        if parent_ctx is not None and parent_ctx.model:
            model_info = get_model(parent_ctx.model, parent_ctx.model_provider)
        if model_info is not None and model_info.context_window:
            return int(model_info.context_window)
        return int(get_harness_config().default_context_window)
    except Exception:
        return None


def _parent_ctx() -> Any:
    from vtx.ai.agent.dispatcher import get_context

    return get_context()


def _load_runner() -> Callable[..., Awaitable[Any]]:
    """Resolve the sub-agent runner, honouring a registered override."""
    from vtx.ai.agent.tools import task as task_module

    return task_module.resolve_subagent_runner()


def _message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if getattr(part, "type", None) == "tool_call":
                parts.append(f"[tool_call:{getattr(part, 'name', '')}]")
                continue
            text = getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    return str(content) if content is not None else ""


def _message_tool_calls(message: Any) -> list[str]:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return []
    return [
        getattr(part, "name", "") for part in content if getattr(part, "type", None) == "tool_call"
    ]


def _preview(message: Any, index: int, max_chars: int) -> dict[str, Any]:
    text = _message_text(message)
    clipped = text[:max_chars]
    preview: dict[str, Any] = {
        "index": index,
        "role": str(getattr(message, "role", "unknown")),
        "text": clipped,
        "truncated": len(text) > max_chars,
    }
    tool_calls = _message_tool_calls(message)
    if tool_calls:
        preview["toolCalls"] = tool_calls
    return preview


# =================================================================================================
# tool.call
# =================================================================================================


async def _handle_tool_catalog(payload: dict[str, Any], ctx: _Ctx) -> Any:
    """Every callable tool's name, description, and input schema, in one reply.

    Backs the kernel's cached discovery catalog, which is what makes
    ``find_tools()`` and ``describe_tool()`` usable from synchronous cell code.
    A cell cannot await a round trip inside a plain function, and searching per
    query would put a bridge call in the middle of every tool lookup, so the
    whole surface crosses once and the kernel ranks it locally.

    Cost is bounded and off-prompt: a few kilobytes of schema against the
    ~24k-char skills catalog the model already carries, fetched once per session
    rather than added to every turn.
    """
    from vtx.ai.agent.rlm.diagnostics import plain_data

    return plain_data({"tools": _tool_catalog()})


def _tool_catalog() -> list[dict[str, Any]]:
    """The registered tool surface as plain ``{name, description, parameters}``."""
    from vtx.ai.agent.tools import get_all_tools

    catalog: list[dict[str, Any]] = []
    for name, tool in sorted(get_all_tools().items()):
        parameters = getattr(tool, "parameters", None)
        if parameters is None:
            params_model = getattr(tool, "params", None)
            schema = getattr(params_model, "model_json_schema", None)
            parameters = schema() if callable(schema) else {}
        catalog.append(
            {
                "name": str(getattr(tool, "name", name)),
                "description": str(getattr(tool, "description", "") or ""),
                "parameters": parameters or {},
            }
        )
    return catalog


async def _handle_tool_call(payload: dict[str, Any], ctx: _Ctx) -> Any:
    from vtx.ai.agent.rlm.diagnostics import (
        HOST_UNAVAILABLE,
        BridgeError,
        classify_bridge_error,
        plain_data,
    )

    name = payload.get("name")
    if not isinstance(name, str) or not name:
        raise HostError("tool.call name must be a non-empty string")
    args = payload.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise HostError("tool.call args must be an object")
    if ctx.tool_executor is None:
        raise BridgeError(
            HOST_UNAVAILABLE,
            "The host bridge is not available in this session, so main-process "
            "tools cannot be called from a cell.",
        )

    available: tuple[str, ...] = ()
    try:
        from vtx.ai.agent.tools import get_all_tools

        available = tuple(get_all_tools())
    except Exception:
        pass

    try:
        result = await ctx.tool_executor(name, args)
    except BaseException as exc:
        # Every bridge failure reaches the model as a category, not a
        # traceback. Unclassified host exceptions are sanitized by
        # ``classify_bridge_error`` rather than forwarded verbatim.
        raise classify_bridge_error(name, exc, available=available) from None
    return plain_data(result)


# =================================================================================================
# rlm.run (admission-only spawn)
# =================================================================================================


def _validate_spawn_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    allowed = {"name", "model", "thinking"}
    unsupported = sorted(set(kwargs) - allowed)
    if unsupported:
        raise HostError(f"Unsupported rlm.spawn kwargs: {', '.join(unsupported)}")
    checked: dict[str, Any] = {}
    for field in ("name", "model", "thinking"):
        value = kwargs.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise HostError(f"rlm.spawn {field} must be a string when provided")
        if field == "name" and not value.strip():
            raise HostError("rlm.spawn name must be a non-empty string")
        checked[field] = value
    return checked


async def _start_child(ctx: _Ctx, prompt: str, kwargs: dict[str, Any]) -> ChildRecord:
    from vtx.ai.agent.background import get_manager
    from vtx.core.paths import get_config_dir

    parent_ctx = _parent_ctx()
    if parent_ctx is None:
        raise HostError(
            "sub-agent dispatch is unavailable: the parent runtime context was never installed"
        )
    manager = parent_ctx.background_manager or get_manager()
    if manager is None:
        raise HostError(
            "Background Task requested but no BackgroundTaskManager is installed. "
            "The headless/runtime must call ConversationRuntime.ensure_background_manager() "
            "before dispatching background sub-agents."
        )

    from dataclasses import replace

    from vtx.ai.agent.tools.task import _resolve_subagent_spec

    spec = _resolve_subagent_spec("", parent_ctx.agent_registry)
    thinking = kwargs.get("thinking")
    if isinstance(thinking, str):
        spec = replace(spec, thinking_level=thinking)
    model_override = kwargs.get("model")
    child_model = model_override or spec.model or parent_ctx.model

    safe_cwd = (parent_ctx.cwd or "").replace("/", "-").replace("\\", "-").strip("-") or "root"
    session_dir = str(get_config_dir() / "tasks" / safe_cwd)

    try:
        record = ctx.registry.spawn(
            name=kwargs.get("name"), session_dir=session_dir, model=child_model, prompt=prompt
        )
    except ChildNameUnavailable as exc:
        raise HostError(str(exc)) from exc

    runner = _load_runner()

    async def _factory() -> Any:
        started = time.monotonic()
        try:
            result = await runner(
                parent_ctx=parent_ctx,
                spec=spec,
                prompt=prompt,
                cancel_event=None,
                model_override=model_override,
                progress_callback=None,
                tool_call_id=f"rlm_{record.rlm_child_id}",
            )
        except asyncio.CancelledError:
            if not (record.deleted and record.status == "cancelled"):
                record.status = "cancelled"
                if record.error is None:
                    record.error = "Cancelled."
            raise
        except Exception as exc:
            if not (record.deleted and record.status == "cancelled"):
                record.status = "error"
                record.error = f"{type(exc).__name__}: {exc}"
            raise

        final_text = getattr(result, "final_text", "") or ""
        error = getattr(result, "error", None)
        duration_ms = getattr(result, "duration_ms", None)
        transcript = getattr(result, "transcript", None) or []
        if record.duration_ms is None:
            record.duration_ms = (
                int(duration_ms)
                if duration_ms is not None
                else int((time.monotonic() - started) * 1000)
            )
        record.tool_use_count = sum(1 for line in transcript if line.strip().startswith("→"))
        if record.deleted and record.status == "cancelled":
            return result
        if error and not final_text:
            record.status = "error"
            record.error = str(error)
        else:
            record.status = "done"
            if final_text:
                record.answer_preview = final_text[:ANSWER_PREVIEW_MAX_CHARS]
            if error is not None:
                record.error = str(error)
        return result

    try:
        task_record = await manager.register(
            description=f"rlm child {record.name}",
            prompt=prompt,
            subagent_type=spec.name,
            model=child_model,
            parent_session_id=ctx.session_id,
            run_coro_factory=_factory,
        )
    except Exception as exc:
        record.status = "error"
        record.error = str(exc) or type(exc).__name__
        raise
    record.task_id = task_record.task_id
    return record


async def _handle_rlm_run(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    prompt = _require_str(payload, "prompt", "rlm.spawn prompt must be a string")
    kwargs = payload.get("kwargs")
    if not isinstance(kwargs, dict):
        kwargs = {}
    kwargs = _validate_spawn_kwargs(kwargs)
    record = await _start_child(ctx, prompt, kwargs)
    return {
        "rlm_child_id": record.rlm_child_id,
        "name": record.name,
        "session_dir": record.session_dir,
        "model": record.model,
    }


async def _handle_rlm_create_session(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    _require_str(payload, "prompt", "rlm.create_session prompt must be a string")
    raise HostError(CREATE_SESSION_UNAVAILABLE)


# =================================================================================================
# bash.completed / bash.consumed
# =================================================================================================


def _positive_pid(payload: dict[str, Any], label: str) -> int:
    pid = payload.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise HostError(f"{label} pid must be a positive integer")
    return pid


def _notice_command(payload: dict[str, Any], label: str) -> str:
    command = payload.get("command")
    if not isinstance(command, str) or not command:
        raise HostError(f"{label} command must be a non-empty string")
    return command


async def _handle_bash_completed(payload: dict[str, Any], ctx: _Ctx) -> None:
    pid = _positive_pid(payload, "bash.completed")
    command = _notice_command(payload, "bash.completed")
    exit_code = payload.get("exitCode")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise HostError("bash.completed exitCode must be an integer")
    text = f"[bash-done pid:{pid} exit:{exit_code}]\n\nCommand: {json.dumps(command)}"
    ctx.registry.add_notice("bash", (pid, command), text)
    return None


async def _handle_bash_consumed(payload: dict[str, Any], ctx: _Ctx) -> None:
    pid = _positive_pid(payload, "bash.consumed")
    command = _notice_command(payload, "bash.consumed")
    ctx.registry.withdraw_notice("bash", (pid, command))
    return None


# =================================================================================================
# rlm.find_models / model.info
# =================================================================================================


def _find_model_entries(query: str, limit: int) -> list[dict[str, str]]:
    from vtx.ai.models import get_all_models
    from vtx.ai.provider_catalog import is_provider_configured, list_providers

    configured = {p.slug for p in list_providers() if is_provider_configured(p)}
    models = [m for m in get_all_models() if m.provider in configured]
    needle = query.strip().lower()
    if needle:
        models = [
            m
            for m in models
            if needle in m.id.lower()
            or needle in m.provider.lower()
            or needle in f"{m.provider}/{m.id}".lower()
        ]
    return [
        {"provider": m.provider, "id": m.id, "name": m.id, "selector": f"{m.provider}/{m.id}"}
        for m in models[:limit]
    ]


async def _handle_rlm_find_models(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    query = payload.get("query")
    if not isinstance(query, str):
        raise HostError("rlm.find_models query must be a string")
    limit = payload.get("limit", DEFAULT_FIND_MODELS_LIMIT)
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit < 1
        or limit > MAX_FIND_MODELS_LIMIT
    ):
        raise HostError(
            f"rlm.find_models limit must be an integer from 1 to {MAX_FIND_MODELS_LIMIT}"
        )
    return {"models": _find_model_entries(query, limit)}


async def _handle_model_info(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    model_id: str | None = None
    provider: str | None = None
    try:
        parent_ctx = _parent_ctx()
        if parent_ctx is not None:
            model_id = parent_ctx.model or None
            provider = parent_ctx.model_provider or None
    except Exception:
        model_id = None
        provider = None
    inputs = ["text"]
    try:
        from vtx.ai.models import get_model

        info = get_model(model_id, provider) if model_id else None
        if info is not None:
            if info.supports_images:
                inputs.append("image")
            if getattr(info, "supports_audio", False):
                inputs.append("audio")
    except Exception:
        pass
    return {"id": model_id, "provider": provider, "input": inputs}


# =================================================================================================
# rlm.list_subagents / rlm.collect / rlm.delete_subagent / rlm.progress.note
# =================================================================================================


async def _handle_rlm_list_subagents(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    return {"subagents": ctx.registry.list_rows()}


async def _handle_rlm_collect(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    raw_targets = payload.get("targets")
    if raw_targets is not None and not isinstance(raw_targets, list):
        raise HostError("rlm.collect targets must be an array of child ids or names")
    targets: list[str] = []
    for target in raw_targets or []:
        if not isinstance(target, str) or not target.strip():
            raise HostError("rlm.collect targets must be non-empty strings")
        targets.append(target.strip())

    raw_timeout = payload.get("timeout_ms")
    if raw_timeout is None:
        timeout_ms = 0
    else:
        if (
            not isinstance(raw_timeout, int)
            or isinstance(raw_timeout, bool)
            or raw_timeout < 0
            or raw_timeout > SAFE_INT_MAX
        ):
            raise HostError(
                "rlm.collect timeout_ms must be a non-negative integer up to 2147483647"
            )
        timeout_ms = raw_timeout

    selected: list[ChildRecord] = []
    results: list[dict[str, Any]] = []
    if not targets:
        selected = ctx.registry.active()
    else:
        for target in targets:
            matches = ctx.registry.find(target)
            if not matches:
                deleted = ctx.registry.find_deleted(target)
                if len(deleted) == 1:
                    results.append(deleted[0].deleted_collect_entry())
                    continue
                if len(deleted) > 1:
                    raise HostError(
                        f'RLM child selector "{target}" is ambiguous in the current parent session'
                    )
                raise HostError(
                    f'No direct RLM child matches "{target}" in the current parent session'
                )
            if len(matches) > 1:
                raise HostError(
                    f'RLM child selector "{target}" is ambiguous in the current parent session'
                )
            selected.append(matches[0])

    if timeout_ms > 0 and any(not child.settled for child in selected):
        deadline = time.monotonic() + timeout_ms / 1000
        while any(not child.settled for child in selected):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.02, remaining))

    results.extend(child.collect_entry() for child in selected)
    return {"results": results}


async def _handle_rlm_progress_note(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise HostError("rlm.progress.note message must be a non-empty string")
    message = message.strip()
    if _js_length(message) > PROGRESS_NOTE_MAX_CHARS:
        raise HostError(
            f"rlm.progress.note message must be at most {PROGRESS_NOTE_MAX_CHARS} characters"
        )
    accepted, retry_after_ms = ctx.registry.note_progress(message)
    if retry_after_ms is None:
        return {"accepted": accepted}
    return {"accepted": accepted, "retry_after_ms": retry_after_ms}


async def _handle_rlm_delete_subagent(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    target = payload.get("target")
    if not isinstance(target, str) or not target.strip():
        raise HostError("rlm.delete_subagent target must be a non-empty string")
    target = target.strip()
    matches = ctx.registry.find(target)
    if not matches:
        raise HostError(f'No direct RLM subagent matches "{target}" in the current parent session')
    if len(matches) > 1:
        raise HostError(
            f'RLM subagent selector "{target}" is ambiguous in the current parent session'
        )
    record = matches[0]
    row = record.list_row()

    record.deleted = True
    if not record.settled:
        record.status = "cancelled"

    if record.task_id and record.status == "cancelled":
        try:
            from vtx.ai.agent.background import get_manager
            from vtx.ai.agent.dispatcher import get_context

            parent_ctx = get_context()
            manager = (parent_ctx.background_manager if parent_ctx else None) or get_manager()
            if manager is not None:
                await manager.cancel(record.task_id)
        except Exception:
            pass
    return {"subagent": row}


# =================================================================================================
# refine.* / compact.*
# =================================================================================================


def _turn_is_active(ctx: _Ctx) -> bool:
    # A host request only arrives while a cell runs, which is inside a turn;
    # a bound kernel session is the host-side proxy for that state.
    return ctx.session_id is not None


async def _handle_refine_status(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    return {
        "pending": ctx.registry.refine_pending is not None,
        "in_flight": ctx.registry.refine_in_flight,
    }


async def _handle_refine_run(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    instructions = payload.get("instructions")
    if instructions is not None and not isinstance(instructions, str):
        raise HostError("refine.run instructions must be a string when provided")
    global_flag = payload.get("global")
    if global_flag is not None and not isinstance(global_flag, bool):
        raise HostError("refine.run global must be a boolean when provided")
    if not _turn_is_active(ctx):
        return {"scheduled": False, "reason": REFINE_NO_TURN_REASON}
    previous = ctx.registry.refine_pending or {}
    ctx.registry.refine_pending = {
        "instructions": instructions if instructions is not None else previous.get("instructions"),
        "global": global_flag if global_flag is not None else previous.get("global"),
    }
    return {"scheduled": True, "note": REFINE_NOTE}


async def _handle_compact_status(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    tokens: int | None = None
    try:
        parent_ctx = _parent_ctx()
        session = getattr(parent_ctx, "session", None) if parent_ctx else None
        if session is not None:
            tokens = int(session.token_totals().context_tokens)
    except Exception:
        tokens = None
    window = _context_window(ctx)
    percent: float | None = None
    if tokens is not None and window:
        percent = round(100.0 * tokens / window, 1)
    return {
        "tokens": tokens,
        "context_window": window,
        "percent": percent,
        "scheduled": ctx.registry.compact_pending is not None,
    }


async def _handle_compact_run(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    instructions = payload.get("instructions")
    if instructions is not None and not isinstance(instructions, str):
        raise HostError("compact.run instructions must be a string when provided")
    if not _turn_is_active(ctx):
        return {"scheduled": False, "reason": COMPACT_NO_TURN_REASON}
    ctx.registry.compact_pending = {"instructions": instructions}
    return {"scheduled": True, "note": COMPACT_NOTE}


# =================================================================================================
# agent_message.*
# =================================================================================================


def _normalize_agent_message(payload: dict[str, Any]) -> str:
    message = payload.get("message")
    if not isinstance(message, str):
        raise HostError("agent_message.send message must be a string")
    trimmed = message.strip()
    if not trimmed:
        raise HostError("Agent session message cannot be empty")
    length = _js_length(trimmed)
    if length > AGENT_MESSAGE_MAX_CHARS:
        raise HostError(
            f"Agent session message is too long: {length} chars exceeds {AGENT_MESSAGE_MAX_CHARS}"
        )
    return trimmed


def _parent_receipt(ctx: _Ctx, message: str) -> dict[str, Any]:
    sender = ctx.session_id or "kernel"
    receipt_id = f"agentmsg_{uuid.uuid4().hex[:16]}"
    notice_text = f"[agent-message from child:{sender}]\n\n{message}"
    ctx.registry.add_notice("agent_message", receipt_id, notice_text)
    return {
        "id": receipt_id,
        "source": "agent_message",
        "target": "parent",
        "from": {"sessionName": sender},
        "message": message,
        "deliveryStatus": "queued",
        "queuedAt": _iso_now(),
        "deliveryMode": "steer",
    }


def _resolve_child_target(ctx: _Ctx, selector: str) -> ChildRecord:
    matches = ctx.registry.find(selector)
    if not matches:
        matches = [
            child
            for child in ctx.registry.active()
            if child.name.endswith(selector) or child.rlm_child_id.endswith(selector)
        ]
    if not matches:
        raise HostError(f"No child matches {json.dumps(selector)}")
    if len(matches) > 1:
        raise HostError(f"child selector {json.dumps(selector)} is ambiguous")
    return matches[0]


async def _handle_agent_message_list_agents(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    raise HostError(AGENT_MESSAGE_LIST_AGENTS_REMOVED)


async def _handle_agent_message_send(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    message = _normalize_agent_message(payload)

    if isinstance(payload.get("target"), str):
        target = payload["target"]
        if target != "all":
            raise HostError(
                "positional agent_message.send targets are not supported; "
                "use receiver_role and receiver_name"
            )
        if payload.get("receiver_role") is not None or payload.get("receiver_name") is not None:
            raise HostError(
                "agent_message.send broadcast cannot be combined with receiver_role/receiver_name"
            )
        receipts: list[dict[str, Any]] = [_parent_receipt(ctx, message)]
        for child in ctx.registry.active():
            receipts.append(
                {
                    "target": child.rlm_child_id,
                    "error": (
                        "child messaging is not available in this session; "
                        "spawned children have no message inbox"
                    ),
                }
            )
        return {"receipts": receipts}

    role = payload.get("receiver_role")
    if role not in ("parent", "sibling", "child"):
        raise HostError('agent_message.send receiver_role must be "parent", "sibling", or "child"')
    receiver_name = payload.get("receiver_name")
    if role == "parent":
        if receiver_name is not None:
            raise HostError("agent_message.send receiver_name must be omitted for parent messages")
        return _parent_receipt(ctx, message)
    if not isinstance(receiver_name, str) or not receiver_name.strip():
        raise HostError(
            "agent_message.send receiver_name is required for sibling and child messages"
        )
    selector = receiver_name.strip()
    if role == "sibling":
        raise HostError(f"No sibling matches {json.dumps(selector)}")
    child = _resolve_child_target(ctx, selector)
    raise HostError(
        f'child messaging is not available in this session: "{child.name}" has no message inbox'
    )


# =================================================================================================
# agent_observe.*
# =================================================================================================


def _require_target(payload: dict[str, Any], label: str) -> str:
    target = payload.get("target")
    if not isinstance(target, str):
        raise HostError(f"{label} target must be a string")
    return target


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int, label: str) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise HostError(f"{label} must be an integer")
    if value < minimum or value > maximum:
        raise HostError(f"{label} must be between {minimum} and {maximum}")
    return value


def _current_summary(ctx: _Ctx) -> dict[str, Any]:
    session_id = ctx.session_id or "current"
    streaming = ctx.session_id is not None
    summary: dict[str, Any] = {
        "sessionId": session_id,
        "runtimeKind": "top-level",
        "status": "running" if streaming else "idle",
        "isCurrent": True,
        "isStreaming": streaming,
        "isCompacting": False,
        "attachedClients": 0,
        "queuedCount": 0,
        "isSessionActive": True,
    }
    try:
        parent_ctx = _parent_ctx()
        cwd = getattr(parent_ctx, "cwd", None) if parent_ctx else None
        if cwd:
            summary["cwd"] = cwd
    except Exception:
        pass
    messages = _session_messages(ctx)
    if messages:
        summary["messageCount"] = len(messages)
        summary["latestMessage"] = _preview(messages[-1], len(messages) - 1, PREVIEW_MAX_CHARS)
    return summary


def _child_summary(child: ChildRecord, ctx: _Ctx) -> dict[str, Any]:
    running = child.status == "running"
    status = "running" if running else ("idle" if child.status == "queued" else "inactive")
    summary: dict[str, Any] = {
        "sessionId": child.rlm_child_id,
        "sessionName": child.name,
        "relationship": "child",
        "runtimeKind": "subagent",
        "status": status,
        "isCurrent": False,
        "isStreaming": running,
        "isCompacting": False,
        "attachedClients": 0,
        "queuedCount": 0,
        "isSessionActive": running,
        "rlmChildId": child.rlm_child_id,
        "repliedSinceTask": child.replied_since_task,
    }
    if running:
        summary["activeSessionId"] = child.rlm_child_id
    if ctx.session_id:
        summary["parentSessionId"] = ctx.session_id
    if child.prompt:
        summary["firstMessage"] = child.prompt[:PREVIEW_MAX_CHARS]
    summary["cwd"] = child.session_dir
    return summary


def _resolve_observe_target(ctx: _Ctx, target: str) -> tuple[str, ChildRecord | None]:
    selector = target.strip()
    pool: list[tuple[str, ChildRecord | None]] = []
    session_id = ctx.session_id
    if selector == "current" or (session_id and selector == session_id):
        pool.append(("current", None))
    for child in ctx.registry.active():
        if (
            child.matches(selector)
            or child.name.endswith(selector)
            or child.rlm_child_id.endswith(selector)
        ):
            pool.append(("child", child))
    if not pool:
        raise HostError(f"No child matches {json.dumps(selector)}")
    if len(pool) > 1:
        raise HostError(f"child selector {json.dumps(selector)} is ambiguous")
    return pool[0]


async def _handle_agent_observe_list(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    current = _current_summary(ctx)
    agents: list[dict[str, Any]] = [current]
    agents.extend(_child_summary(child, ctx) for child in ctx.registry.active())
    return {"current": current, "agents": agents}


async def _handle_agent_observe_get(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    target = _require_target(payload, "agent_observe.get")
    kind, child = _resolve_observe_target(ctx, target)
    if kind == "current":
        return {"agent": _current_summary(ctx)}
    assert child is not None
    return {"agent": _child_summary(child, ctx)}


async def _handle_agent_observe_recent(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    target = _require_target(payload, "agent_observe.recent")
    limit = _clamp_int(
        payload.get("limit"),
        default=OBSERVE_DEFAULT_LIMIT,
        minimum=1,
        maximum=OBSERVE_MAX_LIMIT,
        label="agent_observe limit",
    )
    max_chars = _clamp_int(
        payload.get("max_chars", payload.get("maxChars")),
        default=OBSERVE_DEFAULT_MAX_CHARS,
        minimum=OBSERVE_MIN_MAX_CHARS,
        maximum=OBSERVE_MAX_MAX_CHARS,
        label="agent_observe max_chars",
    )
    kind, child = _resolve_observe_target(ctx, target)

    entries: list[tuple[Any, str]] = []
    if kind == "current":
        entries = list(enumerate(_session_messages(ctx)))
        entries = entries[-limit:] if entries else []
        previews = [_preview(message, index, max_chars) for index, message in entries]
        truncated = bool(previews and any(p["truncated"] for p in previews))
        agent = _current_summary(ctx)
    else:
        assert child is not None
        raw: list[tuple[int, str, str]] = []
        if child.prompt:
            raw.append((0, "user", child.prompt))
        body = child.answer_preview or child.error
        if body:
            raw.append((len(raw), "assistant", body))
        raw = raw[-limit:]
        previews = [
            {
                "index": index,
                "role": role,
                "text": text[:max_chars],
                "truncated": len(text) > max_chars,
            }
            for index, role, text in raw
        ]
        truncated = bool(previews and any(p["truncated"] for p in previews))
        agent = _child_summary(child, ctx)

    return {
        "agent": agent,
        "messages": previews,
        "limit": limit,
        "maxChars": max_chars,
        "truncated": truncated,
    }


# =================================================================================================
# goal.*
# =================================================================================================


def _goal_service() -> Any:
    from vtx.ai.agent.goal import service as goal_service

    return goal_service


def _require_goals_enabled(ctx: _Ctx) -> Any:
    module = _goal_service()
    parent_ctx = _parent_ctx()
    cwd = getattr(parent_ctx, "cwd", None) if parent_ctx else None
    session = getattr(parent_ctx, "session", None) if parent_ctx else None
    session_id = str(getattr(session, "id", "") or "")
    import os

    service = module.get_service(cwd or os.getcwd(), session_id)
    if service.settings.get("disabled"):
        raise HostError(GOALS_DISABLED_MESSAGE)
    return service


def _goal_payload(record: Any) -> dict[str, Any]:
    if record is None:
        return {"goal": None, "remaining_tokens": None, "completion_budget_report": None}
    budget = getattr(record, "token_budget", None)
    used = int(getattr(record, "tokens_used", 0) or 0)
    remaining = max(0, int(budget) - used) if isinstance(budget, int) else None
    return {
        "goal": {
            "goal_id": record.id,
            "objective": record.objective,
            "status": record.status,
            "token_budget": budget,
            "tokens_used": used,
            "time_used_seconds": int(getattr(record, "time_used_seconds", 0) or 0),
            "created_at": getattr(record, "created_at", None),
            "updated_at": getattr(record, "updated_at", None),
        },
        "remaining_tokens": remaining,
        "completion_budget_report": None,
    }


async def _handle_goal(payload: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    request_type = payload.get("type")
    service = _require_goals_enabled(ctx)
    if request_type == "goal.get":
        return _goal_payload(service.focused())
    if request_type == "goal.create":
        objective = payload.get("objective")
        if not isinstance(objective, str):
            raise HostError("goal.create objective must be a string")
        token_budget = payload.get("token_budget")
        if token_budget is not None and (
            not isinstance(token_budget, int) or isinstance(token_budget, bool)
        ):
            raise HostError("goal.create token_budget must be an integer when provided")
        existing = service.focused()
        if existing is not None and existing.status != "complete":
            if existing.status == "paused":
                raise HostError(
                    "cannot create a new goal because a paused goal exists; "
                    "ask the user to resume it with /goal resume or clear it with /goal clear"
                )
            if existing.status == "budget_limited":
                raise HostError(
                    "cannot create a new goal because a budget-limited goal exists; "
                    "ask the user to resume it with /goal resume or clear it with /goal clear"
                )
            raise HostError(
                "cannot create a new goal because this thread already has an active goal; "
                "run `await goal.complete()` when it is achieved, or ask the user to "
                "clear it with /goal clear"
            )
        record = service.create(objective, token_budget=token_budget, source="goal-host")
        return _goal_payload(record)
    if request_type == "goal.complete":
        existing = service.focused()
        if existing is None or existing.status in ("idle", "complete"):
            raise HostError("cannot complete goal because this thread has no goal")
        updated = service.set_status(existing.id, "complete")
        return _goal_payload(updated)
    raise HostError(f'unknown goal request type "{request_type}"')


# =================================================================================================
# dispatch
# =================================================================================================


_HANDLERS: dict[str, Handler] = {
    "tool.call": _handle_tool_call,
    "tool.catalog": _handle_tool_catalog,
    "rlm.run": _handle_rlm_run,
    "rlm.create_session": _handle_rlm_create_session,
    "rlm.find_models": _handle_rlm_find_models,
    "rlm.list_subagents": _handle_rlm_list_subagents,
    "rlm.collect": _handle_rlm_collect,
    "rlm.progress.note": _handle_rlm_progress_note,
    "rlm.delete_subagent": _handle_rlm_delete_subagent,
    "bash.completed": _handle_bash_completed,
    "bash.consumed": _handle_bash_consumed,
    "model.info": _handle_model_info,
    "refine.status": _handle_refine_status,
    "refine.run": _handle_refine_run,
    "compact.status": _handle_compact_status,
    "compact.run": _handle_compact_run,
    "agent_message.list_agents": _handle_agent_message_list_agents,
    "agent_message.send": _handle_agent_message_send,
    "agent_observe.list": _handle_agent_observe_list,
    "agent_observe.get": _handle_agent_observe_get,
    "agent_observe.recent": _handle_agent_observe_recent,
    "goal.get": _handle_goal,
    "goal.create": _handle_goal,
    "goal.complete": _handle_goal,
}


async def dispatch_host_request(
    payload: dict[str, Any],
    *,
    tool_executor: ToolExecutor | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Dispatch one kernel -> host request; never raises.

    Returns ``{"status": "ok", "result": <json>}`` on success and
    ``{"status": "error", "error": <msg>}`` for unknown types, validation
    failures, and any internal exception.
    """
    try:
        request_type = payload.get("type") if isinstance(payload, dict) else None
        handler = _HANDLERS.get(request_type) if isinstance(request_type, str) else None
        if handler is None:
            raise HostError(f'host request type "{request_type}" is not available in this session')
        ctx = _Ctx(
            session_id=session_id, tool_executor=tool_executor, registry=get_registry(session_id)
        )
        result = await handler(payload, ctx)
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc) or type(exc).__name__}


# Alias kept for the ipython manager's ``dispatch`` import.
dispatch = dispatch_host_request

__all__ = [
    "HostError",
    "dispatch",
    "dispatch_host_request",
    "drain_notices",
    "drain_pending_compact",
    "drain_pending_refine",
]
