"""ACP (Agent Client Protocol) server for vtx — JSON-RPC 2.0 over stdio.

ndJSON framing: one JSON object per line on stdin/stdout, matching the ACP
spec used by ``devin acp`` and Zed/JetBrains clients. The agent side
implements ``initialize``, ``authenticate``, ``session/new``,
``session/load``, ``session/prompt``, ``session/cancel``, ``session/set_mode``,
``session/set_model``; agent->client traffic is ``session/update``
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


class _Session:
    """Per-ACP-session agent state."""

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.mode = "default"
        self.model: str | None = None
        self.model_provider: str | None = None
        self.thinking_level: str | None = None
        self.vtx_session_id: str | None = None
        self.session_path: Any = None
        self.runtime: ConversationRuntime | None = None
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

            def _build() -> tuple[Any, str | None]:
                from vtx.agent.runtime import ConversationRuntime
                from vtx.coding_agent.tools import DEFAULT_TOOLS, get_tools_with_extensions
                from vtx.core.config import config, get_last_selected

                last = get_last_selected()
                provider = sess.model_provider or last.provider or config.llm.default_provider
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
                    tools=get_tools_with_extensions(DEFAULT_TOOLS),
                )
                init = runtime.initialize(resume_session=sess.vtx_session_id)
                if init.provider_error:
                    return runtime, init.provider_error
                return runtime, None

            runtime, provider_error = await asyncio.to_thread(_build)
            sess.runtime = runtime
            if provider_error:
                raise RuntimeError(provider_error)
            agent = await asyncio.to_thread(runtime.prepare_for_run)
            if agent is None:
                raise RuntimeError("agent initialization failed")
            sess.agent = agent
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
            case AgentEndEvent():
                pass

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
        """Mode + model pickers the client renders from ``session/new``."""
        model_options: list[dict[str, Any]] = []
        try:
            from vtx.ai.models import get_all_models

            for m in get_all_models():
                uid = f"{m.provider}:{m.id}"
                model_options.append({"value": uid, "name": f"{m.id} ({m.provider})"})
        except Exception:
            pass
        current_model = (
            f"{sess.model_provider}:{sess.model}"
            if sess.model_provider and sess.model
            else sess.model or ""
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
        try:
            from vtx.ai.thinking import THINKING_LEVELS

            options.append(
                {
                    "id": "thinking",
                    "name": "Thinking",
                    "category": "thinking",
                    "type": "select",
                    "currentValue": sess.thinking_level or "",
                    "options": [{"value": lv, "name": lv} for lv in THINKING_LEVELS],
                }
            )
        except Exception:
            pass
        return options

    def session_new(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = uuid.uuid4().hex[:12]
        sess = _Session(cwd=params.get("cwd") or os.getcwd())
        self._sessions[session_id] = sess
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

    def session_set_config_option(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = params.get("sessionId")
        config_id = params.get("configId")
        value = params.get("value")
        sess = self._sessions.get(session_id or "")
        if sess is not None and isinstance(value, str):
            if config_id == "model":
                self.session_set_model({"sessionId": session_id, "modelId": value})
            elif config_id == "mode":
                self.session_set_mode({"sessionId": session_id, "modeId": value})
            elif config_id == "thinking":
                sess.thinking_level = value
        if sess is not None:
            return {"configOptions": self._config_options(sess)}
        return {}

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
        sess.vtx_session_id = info.id
        sess.session_path = info.path
        self._sessions[session_id] = sess
        # Build the runtime (resume) in the background of the response: the
        # client expects history replay first, then the load result.
        import contextlib

        with contextlib.suppress(Exception):
            await self._ensure_agent(sess)
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
        # Tools resolve paths against the process cwd; sessions are used
        # one-at-a-time by ACP clients, so chdir per prompt.
        if sess.cwd and os.path.isdir(sess.cwd):
            os.chdir(sess.cwd)
        try:
            agent = await self._ensure_agent(sess)
        except Exception as exc:
            return {"_error": str(exc)}
        prompt, images = self._prompt_parts(params)
        if not prompt and not images:
            return {"_error": "empty prompt"}

        stop_reason = "end_turn"
        try:
            async for event in agent.run(
                prompt, images=images or None, cancel_event=sess.cancel_event
            ):
                from vtx.core.events import AgentEndEvent

                if isinstance(event, AgentEndEvent):
                    stop_reason = _ACP_STOP_REASONS.get(event.stop_reason.value, "end_turn")
                await self._map_event(session_id, sess, event)
        except asyncio.CancelledError:
            stop_reason = "cancelled"
        except Exception as exc:
            await self._agent_message(session_id, f"\n[error] {exc}\n")
            stop_reason = "refusal"
        return {"stopReason": stop_reason}

    def session_cancel(self, params: dict[str, Any]) -> None:
        sess = self._sessions.get(params.get("sessionId") or "")
        if sess is not None:
            sess.cancel_event.set()

    def session_set_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        sess = self._sessions.get(params.get("sessionId") or "")
        if sess is not None:
            sess.mode = params.get("modeId") or sess.mode
        return {}

    def session_set_model(self, params: dict[str, Any]) -> dict[str, Any]:
        sess = self._sessions.get(params.get("sessionId") or "")
        if sess is not None:
            model_id = params.get("modelId")
            if isinstance(model_id, str) and ":" in model_id:
                provider, _, model = model_id.partition(":")
                sess.model_provider, sess.model = provider, model
            else:
                sess.model = model_id
        return {}

    _METHODS: ClassVar[dict[str, str]] = {
        "initialize": "initialize",
        "authenticate": "initialize",
        "session/new": "session_new",
        "session/load": "session_load",
        "session/prompt": "session_prompt",
        "session/cancel": "session_cancel",
        "session/set_mode": "session_set_mode",
        "session/set_model": "session_set_model",
        "session/list": "session_list",
        "session/delete": "session_delete",
        "session/set_config_option": "session_set_config_option",
    }
    _ASYNC_METHODS = frozenset({"session_prompt", "session_load"})

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
    asyncio.run(AcpServer()._serve())
