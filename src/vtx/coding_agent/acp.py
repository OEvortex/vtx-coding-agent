"""ACP (Agent Client Protocol) server for vtx — JSON-RPC 2.0 over stdio.

ndJSON framing: one JSON object per line on stdin/stdout, matching the ACP
spec used by ``devin acp`` and Zed/JetBrains clients. The agent side
implements session lifecycle, prompt, cancellation, steering, configuration,
listing, and deletion methods; agent->client traffic is ``session/update``
notifications plus ``session/request_permission`` requests.

``session/prompt`` drives a real :class:`ConversationRuntime` turn: events
from ``agent.run()`` stream out as ``session/update`` notifications and the
request resolves with a ``stopReason`` when the turn ends. ``session/cancel``
sets the turn's ``asyncio.Event`` so the agent stops cooperatively.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import uuid
from collections import deque
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from vtx.agent.runtime import ConversationRuntime
    from vtx.core.events import Event
    from vtx.core.permissions import ApprovalResponse

PROTOCOL_VERSION = 1

_ACP_STOP_REASONS = {
    "stop": "end_turn",
    "length": "max_tokens",
    "interrupted": "cancelled",
    "error": "refusal",
}

# Slash commands vtx ships (tui/autocomplete.py). ACP clients render these
# from available_commands_update; session_prompt executes the headless-safe
# subset and answers the rest with where the client equivalent lives.
_BUILTIN_COMMANDS: list[dict[str, Any]] = [
    {"name": n, "description": d, "input": {"hint": f"/{n}"}}
    for n, d in [
        ("help", "show available commands"),
        ("skills", "list loaded skills by category"),
        ("tools", "list loaded tools by source"),
        ("clear", "clear conversation history"),
        ("model", "change model"),
        ("provider", "filter models by provider"),
        ("new", "start new conversation"),
        ("resume", "resume a session"),
        ("session", "show session info and stats"),
        ("usage", "token usage: this session, 30 days, lifetime"),
        ("login", "login to a provider"),
        ("logout", "logout from a provider"),
        ("export", "export session"),
        ("compact", "compact now; /compact <focus> steers the summary"),
        ("recap", "summarize where the session left off"),
        ("agent", "list/switch handoff agents"),
        ("update", "check for and install latest vtx update"),
        ("reload", "re-apply config, extensions, agents, tools, skills"),
    ]
]

# Client-side equivalents for commands that are UI actions in the TUI.
_SLASH_CLIENT_HINT = {
    "clear": "start a new chat",
    "new": "start a new chat",
    "resume": "pick a session from the session list",
    "login": "run `vtx auth login` in a terminal",
    "logout": "run `vtx auth logout` in a terminal",
    "export": "not available over ACP yet",
    "recap": "ask the agent to summarize the session",
    "update": "run `vtx update` in a terminal",
    "reload": "restart the ACP server or reload the window",
}


class _Session:
    """Per-ACP-session agent state."""

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.mode = "default"
        self.model: str | None = None
        self.model_provider: str | None = None
        self.thinking_level: str | None = None
        self.agent_name: str | None = None
        self.acp_id: str = ""
        self.vtx_session_id: str | None = None
        self.session_path: Any = None
        self.runtime: ConversationRuntime | None = None
        self._mcp_task: asyncio.Task[None] | None = None
        self._cmd_task: asyncio.Task[None] | None = None
        self._warm_task: asyncio.Task[None] | None = None
        self.steer_queue: deque[tuple[str, list[Any] | None]] = deque()
        self.steer_event: asyncio.Event | None = None
        self.hook_bridge: Any = None
        self.pending_title: str | None = None
        self.agent: Any = None
        self.cancel_event = asyncio.Event()
        self._runtime_lock = asyncio.Lock()


class AcpServer:
    """NDJSON JSON-RPC agent endpoint."""

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()
        self._client_caps: dict[str, Any] = {}
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    # -- wire I/O ---------------------------------------------------------

    async def _send(self, msg: dict[str, Any]) -> None:
        async with self._write_lock:
            sys.stdout.write(json.dumps(msg) + "\n")
            sys.stdout.flush()

    async def _respond(self, req_id: Any, result: Any) -> None:
        await self._send({"jsonrpc": "2.0", "id": req_id, "result": result})

    async def _error(self, req_id: Any, code: int, message: str) -> None:
        await self._send(
            {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
        )

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request_client(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Agent->client request; resolves when the client responds."""
        self._next_id += 1
        req_id = self._next_id
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = fut
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        try:
            return await fut
        finally:
            self._pending.pop(req_id, None)

    def _resolve_client_response(self, msg: dict[str, Any]) -> None:
        fut = self._pending.get(msg.get("id"))
        if fut is not None and not fut.done():
            if "error" in msg:
                fut.set_result({"outcome": {"outcome": "cancelled"}})
            else:
                fut.set_result(msg.get("result") or {})

    # -- session updates --------------------------------------------------

    async def _update(self, session_id: str, update: dict[str, Any]) -> None:
        await self._notify("session/update", {"sessionId": session_id, "update": update})

    async def _agent_message(self, session_id: str, text: str) -> None:
        await self._update(
            session_id,
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}},
        )

    # -- agent runtime ----------------------------------------------------

    async def _ensure_agent(self, sess: _Session) -> Any:
        """Build the ConversationRuntime + agent for a session, once."""
        async with sess._runtime_lock:
            if sess.agent is not None:
                return sess.agent

            def _build() -> tuple[Any, str | None, Any]:
                from vtx.agent.agents import AgentRegistry, load_all_agents
                from vtx.agent.runtime import ConversationRuntime
                from vtx.coding_agent.tools import DEFAULT_TOOLS, get_tools_with_extensions
                from vtx.core.config import config, get_last_selected
                from vtx.plugins.extensions import load_for_runtime

                last = get_last_selected()
                provider = sess.model_provider or last.provider or config.llm.default_provider

                # Same enrichment headless runs: agent registry, extensions,
                # active agent, so ACP sessions get plugins/MCP/agents too.
                import os as _os

                agent_registry = AgentRegistry()
                loaded_agents, agent_errors = load_all_agents(cwd=sess.cwd)
                agent_registry.agents = loaded_agents
                agent_registry.errors = agent_errors
                desired = (
                    sess.agent_name
                    or _os.environ.get("VTX_AGENT")
                    or (last.agent or None)
                    or (config.agents.default or None)
                )
                if desired:
                    agent_registry.set_active(desired)

                loaded_extensions = load_for_runtime(cwd=sess.cwd)
                ext_tools = list(loaded_extensions.list_extension_tools())
                if agent_registry.active is not None:
                    ext_tools.extend(agent_registry.active.local_tools.values())
                    ext_tools.extend(
                        loaded_extensions.local_tools_for(agent_registry.active.definition.name)
                    )
                tools = (
                    get_tools_with_extensions(DEFAULT_TOOLS, ext_tools)
                    if ext_tools
                    else get_tools_with_extensions(DEFAULT_TOOLS)
                )

                runtime = ConversationRuntime(
                    cwd=sess.cwd,
                    model=sess.model or last.model_id or config.llm.default_model,
                    model_provider=provider,
                    base_url=config.llm.default_base_url or None,
                    thinking_level=(
                        sess.thinking_level
                        or last.thinking_level
                        or config.llm.default_thinking_level
                    ),
                    tools=tools,
                    extensions=loaded_extensions.bus,
                    agent_registry=agent_registry,
                    active_agent=agent_registry.active,
                    agent_extensions=list(loaded_extensions.extensions),
                )
                runtime.set_loaded_extensions(loaded_extensions)
                init = runtime.initialize(resume_session=sess.vtx_session_id)
                if sess.pending_title and runtime.session is not None:
                    runtime.session.set_name(sess.pending_title)
                    sess.pending_title = None
                if init.provider_error:
                    return runtime, init.provider_error, loaded_extensions
                return runtime, None, loaded_extensions

            runtime, provider_error, loaded_extensions = await asyncio.to_thread(_build)
            sess.runtime = runtime
            if provider_error:
                raise RuntimeError(provider_error)

            # Hooks + MCP, mirroring headless: failures warn, never fail the run.
            import contextlib

            with contextlib.suppress(Exception):
                from pathlib import Path

                from vtx.agent.hooks.bridge import HookBridge

                hook_bridge = HookBridge(
                    bus=loaded_extensions.bus,
                    project_path=Path(sess.cwd) / ".vtx" / "hooks.yml",
                    global_path=Path.home() / ".vtx" / "hooks.yml",
                )
                await hook_bridge.load()
                sess.hook_bridge = hook_bridge

            async def _connect_mcp() -> None:
                with contextlib.suppress(Exception):
                    await runtime.connect_mcp()

            sess._mcp_task = asyncio.create_task(_connect_mcp())

            # Publish the agent picker once the registry is loaded.
            try:
                registry = runtime.agent_registry
                agents = getattr(registry, "agents", {}) if registry else {}
                names = (
                    list(agents)
                    if isinstance(agents, dict)
                    else [a.definition.name for a in agents]
                )
                if names:
                    await self._update(
                        sess.acp_id,
                        {
                            "sessionUpdate": "config_option_update",
                            "configOptions": self._config_options(sess),
                        },
                    )
            except Exception:
                pass
            agent = await asyncio.to_thread(runtime.prepare_for_run)
            if agent is None:
                raise RuntimeError("agent initialization failed")
            sess.agent = agent
            # Re-emit commands now that agent-local commands are known.
            if sess.acp_id:
                with contextlib.suppress(Exception):
                    await self._emit_available_commands(sess.acp_id, sess)
            return agent

    # -- event -> ACP mapping ----------------------------------------------

    async def _map_event(self, session_id: str, sess: _Session, event: Event) -> None:
        from vtx.core.events import (
            AgentEndEvent,
            AskUserEvent,
            ErrorEvent,
            TextDeltaEvent,
            ThinkingDeltaEvent,
            ToolApprovalEvent,
            ToolArgsDeltaEvent,
            ToolEndEvent,
            ToolOutputDeltaEvent,
            ToolResultEvent,
            ToolStartEvent,
        )
        from vtx.core.permissions import AskUserResponse

        match event:
            case TextDeltaEvent(delta=delta):
                if delta:
                    await self._agent_message(session_id, delta)
            case ThinkingDeltaEvent(delta=delta):
                if delta:
                    await self._update(
                        session_id,
                        {
                            "sessionUpdate": "agent_thought_chunk",
                            "content": {"type": "text", "text": delta},
                        },
                    )
            case ToolStartEvent(tool_call_id=cid, tool_name=name):
                await self._update(
                    session_id,
                    {
                        "sessionUpdate": "tool_call",
                        "toolCallId": cid,
                        "title": name,
                        "kind": "other",
                        "status": "pending",
                        "rawInput": {},
                    },
                )
            case ToolArgsDeltaEvent(tool_call_id=cid, partial_arguments=partial):
                if partial:
                    await self._update(
                        session_id,
                        {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": cid,
                            "status": "pending",
                            "rawInput": partial,
                        },
                    )
            case ToolEndEvent(tool_call_id=cid, tool_name=name, arguments=args, display=disp):
                await self._update(
                    session_id,
                    {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": cid,
                        "title": disp or name,
                        "status": "completed",
                        "rawInput": args,
                    },
                )
            case ToolOutputDeltaEvent(tool_call_id=cid, delta=delta):
                if delta:
                    await self._update(
                        session_id,
                        {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": cid,
                            "content": [
                                {"type": "content", "content": {"type": "text", "text": delta}}
                            ],
                        },
                    )
            case ToolResultEvent(result=result):
                if result is not None:
                    text = "".join(getattr(p, "text", "") or "" for p in result.content)
                    await self._update(
                        session_id,
                        {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": result.tool_call_id,
                            "status": "failed" if result.is_error else "completed",
                            "content": [
                                {"type": "content", "content": {"type": "text", "text": text}}
                            ],
                        },
                    )
            case ToolApprovalEvent(tool_call_id=cid, tool_name=name, display=disp, future=future):
                if future is None:
                    return
                response = await self._request_permission(session_id, cid, name, disp)
                future.set_result(response)
            case AskUserEvent(future=future):
                if future is not None:
                    future.set_result(AskUserResponse())
            case ErrorEvent(error=err):
                if err:
                    await self._agent_message(session_id, f"\n[error] {err}\n")
            case AgentEndEvent(total_usage=usage):
                if usage is not None:
                    await self._update(
                        session_id,
                        {
                            "sessionUpdate": "usage_update",
                            "used": usage.total_tokens,
                            "size": usage.total_tokens,
                        },
                    )
            case _:
                text = getattr(event, "warning", None) or getattr(event, "error", None)
                if text:
                    await self._update(
                        session_id,
                        {
                            "sessionUpdate": "agent_thought_chunk",
                            "content": {
                                "type": "text",
                                "text": f"[{type(event).__name__}] {text}",
                            },
                        },
                    )

    async def _request_permission(
        self, session_id: str, tool_call_id: str, tool_name: str, display: str
    ) -> ApprovalResponse:
        """Map a vtx tool approval onto ACP ``session/request_permission``."""
        from vtx.core.permissions import ApprovalResponse

        title = display or tool_name
        resp = await self._request_client(
            "session/request_permission",
            {
                "sessionId": session_id,
                "toolCall": {
                    "toolCallId": tool_call_id,
                    "title": title,
                    "kind": "other",
                    "status": "pending",
                    "rawInput": {},
                },
                "options": [
                    {"optionId": "approve", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "deny", "name": "Deny", "kind": "reject_once"},
                ],
            },
        )
        outcome = (resp.get("outcome") or {}).get("outcome")
        option_id = (resp.get("outcome") or {}).get("optionId")
        if outcome == "selected" and option_id == "approve":
            return ApprovalResponse.APPROVE
        return ApprovalResponse.DENY

    # -- method handlers --------------------------------------------------

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        self._client_caps = params.get("clientCapabilities") or {}
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "agentCapabilities": {
                "loadSession": True,
                "promptCapabilities": {"image": True, "audio": False, "embeddedContext": True},
                "mcpCapabilities": {"http": False, "sse": False},
            },
            "authMethods": [],
            "agentInfo": {"name": "vtx", "version": "1.4.0"},
        }

    def _config_options(self, sess: _Session) -> list[dict[str, Any]]:
        """Session controls rendered by ACP clients."""
        model_options: list[dict[str, Any]] = []
        try:
            from vtx.ai.models import get_all_models
            from vtx.core.config import get_config

            provider_filter = get_config().ui.model_provider_filter
            for m in get_all_models():
                if provider_filter and m.provider != provider_filter:
                    continue
                uid = f"{m.provider}:{m.id}"
                model_options.append({"value": uid, "name": f"{m.id} ({m.provider})"})
        except Exception:
            pass
        runtime = sess.runtime
        model_id = runtime.model if runtime is not None else sess.model
        model_provider = runtime.model_provider if runtime is not None else sess.model_provider
        current_model = (
            f"{model_provider}:{model_id}" if model_provider and model_id else model_id or ""
        )
        options = [
            {
                "id": "mode",
                "name": "Mode",
                "category": "mode",
                "type": "select",
                "currentValue": sess.mode,
                "options": [{"value": "default", "name": "Default"}],
            },
            {
                "id": "model",
                "name": "Model",
                "category": "model",
                "type": "select",
                "currentValue": current_model,
                "options": model_options,
            },
        ]
        registry = getattr(runtime, "agent_registry", None) if runtime else None
        agents = getattr(registry, "agents", []) if registry else []
        agent_names = (
            sorted(agents)
            if isinstance(agents, dict)
            else sorted(a.definition.name for a in agents)
        )
        if agent_names:
            active = registry.active.definition.name if registry.active else ""
            options.append(
                {
                    "id": "agent",
                    "name": "Agent",
                    "category": "agent",
                    "type": "select",
                    "currentValue": active,
                    "options": [{"value": "", "name": "Default"}]
                    + [{"value": name, "name": name} for name in agent_names],
                }
            )
        try:
            from vtx.ai.thinking import THINKING_LEVELS

            effective_levels = runtime.effective_thinking_levels if runtime is not None else []
            levels = effective_levels or THINKING_LEVELS
            options.append(
                {
                    "id": "thinking",
                    "name": "Thinking",
                    "category": "thinking",
                    "type": "select",
                    "currentValue": (
                        runtime.thinking_level if runtime is not None else sess.thinking_level
                    )
                    or "",
                    "options": [{"value": lv, "name": lv} for lv in levels],
                }
            )
        except Exception:
            pass
        return options

    async def _collect_commands(self, sess: _Session) -> list[dict[str, Any]]:
        """All slash commands vtx exposes: skills + active agent commands."""
        commands: list[dict[str, Any]] = []
        try:
            from vtx.agent.context.skills import load_skills

            result = await asyncio.to_thread(load_skills, sess.cwd)
            commands.extend(
                {
                    "name": s.name,
                    "description": s.description or "",
                    "input": {"hint": f"/{s.name}"},
                }
                for s in result.skills
                if s.register_cmd
            )
        except Exception:
            pass
        # TUI built-ins, mirrored from tui/autocomplete.py so ACP clients can
        # offer the same command surface. Executable ones run headless; the
        # rest reply with guidance toward the client-side equivalent.
        commands.extend(_BUILTIN_COMMANDS)
        # Agent-local commands (compose_active_commands merges these in).
        try:
            runtime = sess.runtime
            registry = getattr(runtime, "agent_registry", None) if runtime else None
            active = registry.active if registry else None
            if active is not None:
                for name in active.local_commands:
                    commands.append(
                        {
                            "name": name,
                            "description": f"(agent: {active.definition.name})",
                            "input": {"hint": f"/{name}"},
                        }
                    )
        except Exception:
            pass
        return commands

    async def _emit_available_commands(self, session_id: str, sess: _Session) -> None:
        commands = await self._collect_commands(sess)
        if commands:
            await self._update(
                session_id,
                {"sessionUpdate": "available_commands_update", "availableCommands": commands},
            )

    def session_new(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = uuid.uuid4().hex[:12]
        sess = _Session(cwd=params.get("cwd") or os.getcwd())
        sess.acp_id = session_id
        self._sessions[session_id] = sess
        import contextlib

        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            sess._cmd_task = loop.create_task(self._emit_available_commands(session_id, sess))
        return {
            "sessionId": session_id,
            "modes": {
                "currentModeId": sess.mode,
                "availableModes": [{"id": "default", "name": "Default"}],
            },
            "configOptions": self._config_options(sess),
        }

    def session_list(self, params: dict[str, Any]) -> dict[str, Any]:
        from vtx.agent.session import Session
        from vtx.core.paths import get_config_dir

        sessions_dir = get_config_dir() / "sessions"
        rows: list[dict[str, Any]] = []
        if sessions_dir.is_dir():
            for path in sessions_dir.glob("*/*.jsonl"):
                try:
                    info = Session.build_session_info(path)
                except Exception:
                    continue
                if info is None:
                    continue
                rows.append(
                    {
                        "sessionId": info.id,
                        "cwd": info.cwd,
                        "title": info.name or info.first_message or "",
                        "updatedAt": info.modified.isoformat(),
                    }
                )
        rows.sort(key=lambda r: r["updatedAt"], reverse=True)
        return {"sessions": rows}

    def session_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        from vtx.agent.session import Session
        from vtx.core.paths import get_config_dir

        session_id = params.get("sessionId")
        if isinstance(session_id, str):
            sessions_dir = get_config_dir() / "sessions"
            if sessions_dir.is_dir():
                for path in sessions_dir.glob("*/*.jsonl"):
                    try:
                        info = Session.build_session_info(path)
                    except Exception:
                        continue
                    if info is not None and info.id == session_id:
                        info.path.unlink(missing_ok=True)
                        break
        return {}

    async def session_set_config_option(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = params.get("sessionId")
        config_id = params.get("configId")
        value = params.get("value")
        sess = self._sessions.get(session_id or "")
        if sess is None:
            return {"_error": "unknown session"}
        if not isinstance(value, str):
            return {"_error": "config option value must be a string"}

        runtime = sess.runtime
        if config_id == "mode":
            if value != "default":
                return {"_error": f"unsupported mode: {value}"}
            sess.mode = value
        elif config_id == "model":
            model_id = value
            provider = None
            if ":" in value:
                provider, _, model_id = value.partition(":")
            from vtx.ai.models import get_model

            model = get_model(model_id, provider)
            if model is None:
                return {"_error": f"unknown model: {value}"}
            if runtime is not None:
                runtime.switch_model(model)
            sess.model, sess.model_provider = model.id, model.provider
        elif config_id == "thinking":
            from vtx.ai.thinking import THINKING_LEVELS

            levels = runtime.effective_thinking_levels if runtime is not None else []
            if value not in (levels or THINKING_LEVELS):
                return {"_error": f"unsupported thinking level: {value}"}
            sess.thinking_level = value
            if runtime is not None:
                runtime.set_thinking_level(value)
        elif config_id == "agent":
            agent_name = value or None
            if (
                runtime is not None
                and agent_name
                and runtime.agent_registry.by_name(agent_name) is None
            ):
                return {"_error": f"unknown agent: {agent_name}"}
            sess.agent_name = agent_name
            if runtime is not None:
                runtime.set_active_agent(agent_name)
                await self._emit_available_commands(sess.acp_id, sess)
        else:
            return {"_error": f"unsupported config option: {config_id}"}
        return {"configOptions": self._config_options(sess)}

    def _find_vtx_session(self, session_id: str) -> Any:
        """Locate a stored vtx session by id across all cwd buckets."""
        from vtx.agent.session import Session
        from vtx.core.paths import get_config_dir

        sessions_dir = get_config_dir() / "sessions"
        if not sessions_dir.is_dir():
            return None
        for path in sessions_dir.glob("*/*.jsonl"):
            try:
                info = Session.build_session_info(path)
            except Exception:
                continue
            if info is not None and info.id == session_id:
                return info
        return None

    async def _replay_session(self, session_id: str, sess: _Session) -> None:
        """Emit stored history as session/update notifications (ACP replay)."""
        from vtx.agent.session import Session
        from vtx.protocol.types import TextContent, ToolCall, UserMessage

        if sess.session_path is None:
            return
        try:
            stored = await asyncio.to_thread(Session.load, sess.session_path)
        except Exception:
            return
        for entry in stored.entries:
            message = getattr(entry, "message", None)
            if message is None:
                continue
            if isinstance(message, UserMessage):
                content = message.content
                text = (
                    content
                    if isinstance(content, str)
                    else "".join(p.text for p in content if isinstance(p, TextContent))
                )
                if text:
                    await self._update(
                        session_id,
                        {
                            "sessionUpdate": "user_message_chunk",
                            "content": {"type": "text", "text": text},
                        },
                    )
                continue
            role = getattr(message, "role", None)
            if role != "assistant":
                continue
            for part in getattr(message, "content", []) or []:
                if isinstance(part, TextContent) and part.text:
                    await self._agent_message(session_id, part.text)
                elif isinstance(part, ToolCall):
                    await self._update(
                        session_id,
                        {
                            "sessionUpdate": "tool_call",
                            "toolCallId": part.id,
                            "title": part.name,
                            "kind": "other",
                            "status": "completed",
                            "rawInput": part.arguments,
                        },
                    )

    async def session_load(self, params: dict[str, Any]) -> dict[str, Any]:
        raw_id = params.get("sessionId")
        session_id = raw_id if isinstance(raw_id, str) else ""
        info = self._find_vtx_session(session_id)
        if info is None:
            return {"_error": f"session not found: {session_id}"}
        sess = _Session(cwd=info.cwd)
        sess.acp_id = session_id
        sess.vtx_session_id = info.id
        sess.session_path = info.path
        self._sessions[session_id] = sess
        # Build the runtime (resume) in the background of the response: the
        # client expects history replay first, then the load result. Awaiting
        # it here would hold session/load for the whole build, which is what a
        # "Waking session…" client shows; the first prompt awaits the same
        # runtime through _runtime_lock instead.
        import contextlib

        async def _warm() -> None:
            with contextlib.suppress(Exception):
                await self._ensure_agent(sess)

        sess._warm_task = asyncio.create_task(_warm())
        await self._replay_session(session_id, sess)
        return {}

    def _prompt_parts(self, params: dict[str, Any]) -> tuple[str, list[Any]]:
        """Split an ACP prompt into text and vtx ImageContent parts."""
        from vtx.protocol.types import ImageContent

        texts: list[str] = []
        images: list[Any] = []
        for block in params.get("prompt", []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif block.get("type") == "image":
                data = block.get("data")
                if isinstance(data, str) and data:
                    images.append(
                        ImageContent(data=data, mime_type=block.get("mimeType") or "image/png")
                    )
        return "\n".join(t for t in texts if t), images

    async def session_prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        raw_id = params.get("sessionId")
        session_id = raw_id if isinstance(raw_id, str) else ""
        sess = self._sessions.get(session_id)
        if sess is None:
            return {"_error": "unknown session"}
        sess.cancel_event.clear()
        sess.steer_event = asyncio.Event()
        # Tools resolve paths against the process cwd; sessions are used
        # one-at-a-time by ACP clients, so chdir per prompt.
        if sess.cwd and os.path.isdir(sess.cwd):
            os.chdir(sess.cwd)
        prompt, images = self._prompt_parts(params)
        if not prompt and not images:
            return {"_error": "empty prompt"}

        # Built-in slash commands run headless; they never reach the model, and
        # most never need the runtime either, so they are answered before
        # _ensure_agent pays for building it.
        if prompt.startswith("/") and not images:
            text = await self._slash_command(session_id, sess, prompt)
            if text is not None:
                await self._agent_message(session_id, text)
                return {"stopReason": "end_turn"}

        try:
            agent = await self._ensure_agent(sess)
        except Exception as exc:
            return {"_error": str(exc)}

        pending: deque[tuple[str, list[Any] | None]] = deque([(prompt, images or None)])
        stop_reason = "end_turn"
        try:
            while pending:
                current_prompt, current_images = pending.popleft()
                stop_reason = "end_turn"
                async for event in agent.run(
                    current_prompt,
                    images=current_images,
                    cancel_event=sess.cancel_event,
                    steer_event=sess.steer_event,
                ):
                    from vtx.core.events import AgentEndEvent

                    if isinstance(event, AgentEndEvent):
                        stop_reason = _ACP_STOP_REASONS.get(event.stop_reason.value, "end_turn")
                    await self._map_event(session_id, sess, event)
                if sess.cancel_event.is_set():
                    break
                # A steer interrupted the turn: queued follow-ups become the
                # next prompts, same as the TUI's steer queue.
                while sess.steer_queue:
                    sp, si = sess.steer_queue.popleft()
                    pending.append((sp, si))
                sess.steer_event.clear()
        except asyncio.CancelledError:
            stop_reason = "cancelled"
        except Exception as exc:
            await self._agent_message(session_id, f"\n[error] {exc}\n")
            stop_reason = "refusal"
        return {"stopReason": stop_reason}

    async def _slash_command(self, session_id: str, sess: _Session, prompt: str) -> str | None:
        """Execute a built-in ``/cmd`` headlessly; None means not ours."""
        parts = prompt.lstrip("/").split(None, 1)
        name = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""
        known = {c["name"] for c in _BUILTIN_COMMANDS}
        if name not in known:
            return None  # skill or agent command: let the model/agent handle it

        if name == "help":
            lines = [f"/{c['name']} — {c['description']}" for c in _BUILTIN_COMMANDS]
            return "Available commands:\n" + "\n".join(lines)
        if name == "provider":
            return await self._provider_command(session_id, sess, arg)
        if name in _SLASH_CLIENT_HINT:
            return f"`/{name}` is a client-side command: {_SLASH_CLIENT_HINT[name]}."

        # These two read session state only, so they answer without paying for
        # the runtime build the rest of the commands need.
        if name == "session":
            info = self._find_vtx_session(session_id)
            if info is None:
                return "session info unavailable (not persisted yet)."
            return (
                f"Session {info.id}\n"
                f"cwd: {info.cwd}\n"
                f"messages: {info.message_count}\n"
                f"created: {info.created.isoformat()}\n"
                f"modified: {info.modified.isoformat()}"
            )
        if name == "model":
            return (
                f"current model: {sess.model or 'default'}\n"
                "use the model picker (config option) to switch."
            )

        try:
            agent = await self._ensure_agent(sess)
        except Exception as exc:
            return f"error: {exc}"

        if name == "compact":
            runtime = sess.runtime
            if runtime is not None and hasattr(runtime, "compact_now"):
                try:
                    await runtime.compact_now(arg or None)
                    return "Context compacted."
                except Exception as exc:
                    return f"compact failed: {exc}"
            return "compaction is not available for this session."
        if name == "usage":
            totals = None
            if sess.runtime is not None and sess.runtime.session is not None:
                totals = sess.runtime.session.token_totals()
            if totals is None:
                return "no usage recorded yet."
            return (
                f"Session tokens — input: {totals.input_tokens}, "
                f"output: {totals.output_tokens}, total: {totals.total_tokens}."
            )
        if name == "agent":
            runtime = sess.runtime
            registry = getattr(runtime, "agent_registry", None) if runtime else None
            if registry is None or not registry.agents:
                return "no agents configured."
            active = registry.active.definition.name if registry.active else "none"
            names = ", ".join(registry.agents)
            return f"active agent: {active}\navailable: {names}"
        if name == "skills":
            commands = [c for c in await self._collect_commands(sess)]
            return "Skills/commands:\n" + "\n".join(f"/{c['name']}" for c in commands)
        if name == "tools":
            tools = getattr(agent, "tools", None) or getattr(sess.runtime, "tools", None) or []
            names = sorted(getattr(t, "tool_name", getattr(t, "name", "?")) for t in tools)
            return f"Tools ({len(names)}):\n" + "\n".join(names)
        return f"`/{name}` is not supported over ACP."

    async def _provider_command(self, session_id: str, sess: _Session, arg: str) -> str:
        """``/provider`` headless: report, set, or clear the model provider filter.

        The TUI opens a picker for this; over ACP the client owns pickers, so
        the command takes the slug directly (``/provider kilo``) and bare
        ``/provider`` reports the current filter and the valid slugs.
        """
        from vtx.ai.models import get_all_models
        from vtx.ai.provider_catalog import list_providers
        from vtx.core.config import get_config, set_model_provider_filter

        providers = list_providers()
        slugs = ", ".join(p.slug for p in providers) or "none"
        current = get_config().ui.model_provider_filter
        a = arg.split()[0].lower() if arg.split() else ""

        async def _notify_options() -> None:
            await self._update(
                session_id,
                {
                    "sessionUpdate": "config_option_update",
                    "configOptions": self._config_options(sess),
                },
            )

        if a in ("reset", "all", "clear"):
            set_model_provider_filter("")
            await _notify_options()
            return "Provider filter cleared - the model picker shows all providers."
        if not a:
            state = (
                f"current filter: {current}"
                if current
                else "no filter (the model picker shows all providers)"
            )
            return (
                f"{state}\nProviders: {slugs}\n"
                "Use `/provider <slug>` to scope the model picker to one provider, "
                "or `/provider reset` to clear the filter."
            )
        if a not in {p.slug for p in providers}:
            return f"Unknown provider: {a}. Providers: {slugs}"
        set_model_provider_filter(a)
        await _notify_options()
        count = sum(1 for m in get_all_models() if m.provider == a)
        if count:
            return (
                f"Provider set to {a} - the model picker now shows only {a} "
                f"models ({count} available)."
            )
        return (
            f"Provider set to {a}, but no models are cached for it yet, so the "
            "picker will be empty until its catalog is fetched."
        )

    def session_cancel(self, params: dict[str, Any]) -> None:
        sess = self._sessions.get(params.get("sessionId") or "")
        if sess is not None:
            sess.cancel_event.set()

    def session_steer(self, params: dict[str, Any]) -> None:
        """Queue a mid-turn steer message (TUI parity)."""
        sess = self._sessions.get(params.get("sessionId") or "")
        if sess is None:
            return
        text = params.get("text") or params.get("prompt") or ""
        if isinstance(text, str) and text:
            sess.steer_queue.append((text, None))
        if sess.steer_event is not None:
            sess.steer_event.set()

    async def session_rename(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = params.get("sessionId")
        title = params.get("title")
        if not (isinstance(session_id, str) and isinstance(title, str) and title):
            return {}
        sess = self._sessions.get(session_id)
        info = self._find_vtx_session(session_id)
        if info is not None:
            from vtx.agent.session import Session

            stored = Session.load(info.path)
            stored.set_name(title)
        elif sess is not None:
            # No vtx session file yet: apply the title once the runtime
            # creates one on first prompt.
            sess.pending_title = title
        if sess is not None:
            await self._update(
                session_id, {"sessionUpdate": "session_info_update", "title": title}
            )
        return {}

    async def _vtx_rules_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """AGENTS.md/CLAUDE.md context files as ACP-style rules."""
        sess = self._sessions.get(params.get("sessionId") or "")
        cwd = sess.cwd if sess is not None else os.getcwd()
        try:
            from vtx.agent.context.agent_mds import load_agent_mds
            from vtx.core.paths import get_config_dir

            global_dir = str(get_config_dir())
            rules = [
                {
                    "path": cf.path,
                    "name": os.path.basename(cf.path),
                    "provider": "vtx",
                    "trigger": "always",
                    "scope": "global" if cf.path.startswith(global_dir) else "project",
                }
                for cf in await asyncio.to_thread(load_agent_mds, cwd)
            ]
            return {"rules": rules}
        except Exception:
            return {"rules": []}

    async def _vtx_hooks_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Loaded YAML hooks as ACP-style hook entries."""
        sess = self._sessions.get(params.get("sessionId") or "")
        bridge = sess.hook_bridge if sess is not None else None
        if bridge is None or bridge.registry is None:
            return {"hooks": []}
        try:
            from vtx.agent.hooks.types import HOOK_EVENTS

            hooks: list[dict[str, Any]] = []
            seen: set[int] = set()
            for event in HOOK_EVENTS:
                for h in await bridge.registry.get_hooks(event):
                    if id(h) in seen:
                        continue
                    seen.add(id(h))
                    cfg = h.config
                    hooks.append(
                        {
                            "name": cfg.command or cfg.prompt_text or cfg.type,
                            "sourcePath": "",
                            "events": [cfg.event],
                            "scope": cfg.source,
                            "format": cfg.type,
                        }
                    )
            return {"hooks": hooks}
        except Exception:
            return {"hooks": []}

    async def session_set_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self.session_set_config_option(
            {
                "sessionId": params.get("sessionId"),
                "configId": "mode",
                "value": params.get("modeId"),
            }
        )

    async def session_set_model(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self.session_set_config_option(
            {
                "sessionId": params.get("sessionId"),
                "configId": "model",
                "value": params.get("modelId"),
            }
        )

    _METHODS: ClassVar[dict[str, str]] = {
        "initialize": "initialize",
        "authenticate": "initialize",
        "session/new": "session_new",
        "session/load": "session_load",
        "session/prompt": "session_prompt",
        "session/cancel": "session_cancel",
        "session/steer": "session_steer",
        "session/rename": "session_rename",
        "session/set_mode": "session_set_mode",
        "session/set_model": "session_set_model",
        "session/list": "session_list",
        "session/delete": "session_delete",
        "session/set_config_option": "session_set_config_option",
        "_vtx.ai/rules/list": "_vtx_rules_list",
        "_vtx.ai/hooks/list": "_vtx_hooks_list",
    }
    _ASYNC_METHODS = frozenset(
        {
            "session_prompt",
            "session_load",
            "session_rename",
            "session_set_config_option",
            "session_set_mode",
            "session_set_model",
            "_vtx_rules_list",
            "_vtx_hooks_list",
        }
    )

    async def dispatch(self, req: dict[str, Any]) -> None:
        method = req.get("method")
        params = req.get("params") or {}
        req_id = req.get("id")
        is_notification = "id" not in req
        handler_name = self._METHODS.get(method or "")
        handler = getattr(self, handler_name, None) if handler_name else None
        if handler is None:
            if not is_notification:
                await self._error(req_id, -32601, f"method not found: {method}")
            return
        try:
            if handler_name in self._ASYNC_METHODS:
                result = await handler(params)
            else:
                result = handler(params)
                if asyncio.iscoroutine(result):
                    result = await result
        except Exception as exc:
            if not is_notification:
                await self._error(req_id, -32603, str(exc))
            return
        if isinstance(result, dict) and "_error" in result:
            await self._error(req_id, -32602, result["_error"])
            return
        if not is_notification:
            await self._respond(req_id, result if result is not None else {})

    async def _serve(self) -> None:
        """Read ndJSON requests from stdin until EOF."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def _reader() -> None:
            for line in sys.stdin:
                loop.call_soon_threadsafe(queue.put_nowait, line)
            loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=_reader, daemon=True).start()
        while True:
            line = await queue.get()
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "method" in msg:
                # Long-running requests (session/prompt) run as tasks so
                # session/cancel and client responses keep flowing.
                if self._METHODS.get(msg.get("method") or "") in self._ASYNC_METHODS:
                    task = asyncio.create_task(self.dispatch(msg))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                else:
                    await self.dispatch(msg)
            elif "id" in msg:
                self._resolve_client_response(msg)


def run_acp() -> None:
    """Entry point for ``vtx acp``."""
    from vtx.core.session_retention import prune_in_background

    prune_in_background()
    asyncio.run(AcpServer()._serve())
