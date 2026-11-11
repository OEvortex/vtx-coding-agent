"""Devin provider — credentials, Connect-RPC client, and BaseProvider.

Reads the ``devin-session-token$...`` credential the Devin CLI stores in
``~/.local/share/devin/credentials.toml`` (written by ``devin auth login``)
and calls ``ApiServerService/GetChatMessage`` on ``server.codeium.com`` —
the same Connect-RPC protobuf backend the CLI uses. Proto schemas are loaded
lazily from the vendored descriptor set in :mod:`vtx.ai.oauth.devin.proto`.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import json
import os
import secrets
import subprocess
import time
import tomllib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import httpx

from vtx.ai.base import BaseProvider, LLMStream, ProviderConfig
from vtx.ai.providers.sanitize import sanitize_surrogates
from vtx.protocol.errors import format_error
from vtx.protocol.types import (
    AssistantMessage,
    ImageContent,
    Message,
    StopReason,
    StreamDone,
    StreamError,
    StreamPart,
    TextContent,
    TextPart,
    ThinkingContent,
    ThinkPart,
    ToolCall,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    ToolDefinition,
    ToolResultMessage,
    Usage,
    UserMessage,
)

from . import proto as dp

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

_DEFAULT_API_SERVER_URL = "https://server.codeium.com"
_DEFAULT_DEVIN_API_URL = "https://api.devin.ai"
_DEFAULT_WEBAPP_HOST = "app.devin.ai"


@dataclass
class DevinCredentials:
    token: str
    api_server_url: str = _DEFAULT_API_SERVER_URL
    devin_api_url: str = _DEFAULT_DEVIN_API_URL
    webapp_host: str = _DEFAULT_WEBAPP_HOST


def get_devin_native_path() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(data_home) if data_home else Path.home() / ".local" / "share"
    return base / "devin" / "credentials.toml"


def get_devin_auth_path() -> Path:
    return get_devin_native_path()


def _jwt_exp(token: str) -> float | None:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        exp = data.get("exp")
        return float(exp) if isinstance(exp, (int, float)) else None
    except Exception:
        return None


def load_devin_credentials() -> DevinCredentials | None:
    env_token = os.environ.get("WINDSURF_API_KEY", "").strip()
    env_server = os.environ.get("WINDSURF_API_SERVER_URL", "").strip()
    path = get_devin_native_path()
    token = ""
    api_server_url = _DEFAULT_API_SERVER_URL
    devin_api_url = _DEFAULT_DEVIN_API_URL
    webapp_host = _DEFAULT_WEBAPP_HOST
    if path.exists():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            token = str(data.get("windsurf_api_key") or "")
            api_server_url = str(data.get("api_server_url") or api_server_url)
            devin_api_url = str(data.get("devin_api_url") or devin_api_url)
            webapp_host = str(data.get("devin_webapp_host") or webapp_host)
        except (OSError, tomllib.TOMLDecodeError):
            pass
    if env_token:
        token = env_token
    if env_server:
        api_server_url = env_server
    if not token:
        return None
    return DevinCredentials(
        token=token,
        api_server_url=api_server_url.rstrip("/"),
        devin_api_url=devin_api_url.rstrip("/"),
        webapp_host=webapp_host,
    )


def is_devin_logged_in() -> bool:
    return load_devin_credentials() is not None


def get_valid_devin_token_sync() -> str | None:
    creds = load_devin_credentials()
    if creds is None:
        return None
    exp = _jwt_exp(creds.token.split("$", 1)[-1])
    if exp is not None and time.time() >= exp - 60:
        return None
    return creds.token


def login() -> bool:
    """Run ``devin auth login`` (interactive PKCE browser flow)."""
    try:
        result = subprocess.run(["devin", "auth", "login"], check=False, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and is_devin_logged_in()


def logout() -> bool:
    try:
        result = subprocess.run(["devin", "auth", "logout"], check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Connect-RPC transport
# ---------------------------------------------------------------------------

CLIENT_NAME = "chisel"
CLIENT_VERSION = "3000.12.43"
_SERVICE = "exa.api_server_pb.ApiServerService"
_CHAT_PATH = f"/{_SERVICE}/GetChatMessage"
_MODELS_PATH = f"/{_SERVICE}/GetCascadeModelConfigs"
_STREAM_TIMEOUT = httpx.Timeout(600.0, connect=30.0)
_UNARY_TIMEOUT = httpx.Timeout(60.0, connect=30.0)


def _envelope(payload: bytes) -> bytes:
    return b"\x00" + len(payload).to_bytes(4, "big") + payload


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Basic {token}-{token}", "Connect-Protocol-Version": "1"}


def _make_metadata(token: str) -> Any:
    meta = dp.message_class("Metadata")()
    meta.api_key = token
    meta.extension_name = CLIENT_NAME
    meta.extension_version = CLIENT_VERSION
    meta.ide_name = CLIENT_NAME
    meta.ide_version = CLIENT_VERSION
    meta.locale = "en"
    meta.os = "mac"
    meta.f = secrets.token_hex(366)
    return meta


async def _unary_call(method_path: str, request: Any, token: str, base_url: str) -> bytes:
    url = base_url.rstrip("/") + method_path
    headers = {**_auth_headers(token), "Content-Type": "application/proto"}
    async with httpx.AsyncClient(timeout=_UNARY_TIMEOUT) as client:
        resp = await client.post(url, content=request.SerializeToString(), headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Devin {method_path.rsplit('/', 1)[-1]} HTTP {resp.status_code}: {resp.text[:500]!r}"
        )
    return resp.content


async def _stream_chat(request: Any, token: str, base_url: str) -> AsyncIterator[Any]:
    """POST ``GetChatMessage`` and yield decoded ``GetChatMessageResponse`` frames."""
    url = base_url.rstrip("/") + _CHAT_PATH
    body = _envelope(request.SerializeToString())
    headers = {**_auth_headers(token), "Content-Type": "application/connect+proto"}
    response_cls = dp.message_class("GetChatMessageResponse")

    async with (
        httpx.AsyncClient(timeout=_STREAM_TIMEOUT) as client,
        client.stream("POST", url, content=body, headers=headers) as resp,
    ):
        if resp.status_code != 200:
            text = await resp.aread()
            raise RuntimeError(f"Devin GetChatMessage HTTP {resp.status_code}: {text[:500]!r}")
        buf = bytearray()
        async for chunk in resp.aiter_bytes():
            buf.extend(chunk)
            while len(buf) >= 5:
                flags = buf[0]
                length = int.from_bytes(buf[1:5], "big")
                if len(buf) < 5 + length:
                    break
                payload = bytes(buf[5 : 5 + length])
                del buf[: 5 + length]
                if flags & 0x02:  # trailer frame (JSON)
                    with contextlib.suppress(json.JSONDecodeError):
                        trailer = json.loads(payload.decode("utf-8", "replace"))
                        if trailer.get("error"):
                            err = trailer["error"]
                            raise RuntimeError(
                                f"Devin GetChatMessage {err.get('code', 'unknown')}: "
                                f"{err.get('message', '')}"
                            )
                    continue
                if flags & 0x01:
                    payload = gzip.decompress(payload)
                yield response_cls.FromString(payload)


async def list_devin_models() -> list[dict[str, Any]]:
    """Return ``[{id, name, supports_images, max_tokens}]`` for the account."""
    creds = load_devin_credentials()
    if creds is None:
        return []
    req = dp.message_class("GetCascadeModelConfigsRequest")()
    req.metadata.CopyFrom(_make_metadata(creds.token))
    raw = await _unary_call(_MODELS_PATH, req, creds.token, creds.api_server_url)
    resp = dp.message_class("GetCascadeModelConfigsResponse").FromString(raw)

    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cfg in resp.client_model_configs:
        if cfg.disabled:
            continue
        uid = cfg.model_uid
        if not uid and cfg.model_or_alias is not None:
            uid = cfg.model_or_alias.model_uid
        if not uid or uid in seen:
            continue
        seen.add(uid)
        models.append(
            {
                "id": uid,
                "name": cfg.label or uid,
                "supports_images": bool(cfg.supports_images),
                "max_tokens": int(cfg.max_tokens) if cfg.max_tokens else 0,
            }
        )
    return models


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

_SOURCE_USER = 1  # CHAT_MESSAGE_SOURCE_USER
_SOURCE_SYSTEM = 2  # CHAT_MESSAGE_SOURCE_SYSTEM (assistant history)
_SOURCE_TOOL = 4  # CHAT_MESSAGE_SOURCE_TOOL

_REQUEST_TYPE_CASCADE = 5  # CHAT_MESSAGE_REQUEST_TYPE_CASCADE
_PLANNER_MODE_DEFAULT = 1  # CONVERSATIONAL_PLANNER_MODE_DEFAULT
_TRAJECTORY_TYPE_CASCADE = 4  # CORTEX_TRAJECTORY_TYPE_CASCADE
_STEP_TYPE_USER_INPUT = 14  # CORTEX_STEP_TYPE_USER_INPUT

_STOP_MAX_TOKENS = {3, 7, 9}  # MAX_TOKENS / MAX_NEWLINES / PARTIAL
_STOP_TOOL_USE = {10}  # FUNCTION_CALL
_STOP_ERROR = {12, 13}  # CONTENT_FILTER / ERROR


class DevinSDKProvider(BaseProvider):
    name = "devin"
    reasoning_style: ClassVar[str | None] = None

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        creds = load_devin_credentials()
        token = (config.api_key or "").strip() or (creds.token if creds else "")
        if not token:
            raise ValueError(
                "No Devin credentials found. Run `devin auth login` or set WINDSURF_API_KEY."
            )
        self._token = token
        self._base_url = (
            (config.base_url or "").strip()
            or (creds.api_server_url if creds else "")
            or _DEFAULT_API_SERVER_URL
        ).rstrip("/")

    # -- request building -------------------------------------------------

    def _make_prompt(
        self,
        source: int,
        text: str,
        *,
        tool_calls: list[Any] | None = None,
        tool_call_id: str | None = None,
        tool_result_is_error: bool = False,
        images: list[Any] | None = None,
        thinking: str | None = None,
        signature: str | None = None,
        thinking_redacted: bool = False,
    ) -> Any:
        prompt = dp.message_class("ChatMessagePrompt")()
        prompt.message_id = str(uuid.uuid4())
        prompt.source = source
        prompt.prompt = text
        if tool_calls:
            prompt.tool_calls.extend(tool_calls)
        if tool_call_id:
            prompt.tool_call_id = tool_call_id
        if tool_result_is_error:
            prompt.tool_result_is_error = True
        if images:
            prompt.images.extend(images)
        if thinking is not None:
            prompt.thinking = thinking
        if signature is not None:
            prompt.signature = signature
        if thinking_redacted:
            prompt.thinking_redacted = True
        return prompt

    @staticmethod
    def _image_data(item: ImageContent) -> Any:
        img = dp.message_class("ImageData")()
        data = item.data
        if data.startswith("data:") and "," in data:
            data = data.split(",", 1)[1]
        img.base64_data = data
        img.mime_type = item.mime_type or "image/png"
        return img

    @staticmethod
    def _tool_call_proto(call: ToolCall) -> Any:
        tc = dp.message_class("ChatToolCall")()
        tc.id = call.id
        tc.name = call.name
        tc.arguments_json = json.dumps(call.arguments)
        return tc

    def _convert_messages(self, messages: list[Message]) -> list[Any]:
        last_assistant = -1
        for i, msg in enumerate(messages):
            if isinstance(msg, AssistantMessage):
                last_assistant = i

        prompts: list[Any] = []
        for i, msg in enumerate(messages):
            attach_images = i > last_assistant
            if isinstance(msg, UserMessage):
                prompts.extend(self._user_prompts(msg, attach_images))
            elif isinstance(msg, AssistantMessage):
                prompts.append(self._assistant_prompt(msg))
            elif isinstance(msg, ToolResultMessage):
                prompts.append(self._tool_result_prompt(msg, attach_images))
        return prompts

    def _user_prompts(self, msg: UserMessage, attach_images: bool) -> list[Any]:
        if isinstance(msg.content, str):
            return [self._make_prompt(_SOURCE_USER, sanitize_surrogates(msg.content) or " ")]
        parts: list[str] = []
        images: list[Any] = []
        for item in msg.content:
            if isinstance(item, TextContent):
                t = sanitize_surrogates(item.text)
                if t:
                    parts.append(t)
            elif isinstance(item, ImageContent):
                if attach_images:
                    images.append(self._image_data(item))
                else:
                    parts.append("[Image omitted from history]")
        return [self._make_prompt(_SOURCE_USER, "\n".join(parts), images=images or None)]

    def _assistant_prompt(self, msg: AssistantMessage) -> Any:
        parts: list[str] = []
        tool_calls: list[Any] = []
        thinking_parts: list[str] = []
        signature: str | None = None
        thinking_redacted = False
        for item in msg.content:
            if isinstance(item, TextContent):
                if item.text.strip():
                    parts.append(sanitize_surrogates(item.text))
            elif isinstance(item, ThinkingContent):
                if item.thinking:
                    thinking_parts.append(item.thinking)
                if item.signature:
                    signature = item.signature
                thinking_redacted = thinking_redacted or bool(item.redacted)
            elif isinstance(item, ToolCall):
                tool_calls.append(self._tool_call_proto(item))
        return self._make_prompt(
            _SOURCE_SYSTEM,
            "".join(parts),
            tool_calls=tool_calls or None,
            thinking="\n".join(thinking_parts) or None,
            signature=signature,
            thinking_redacted=thinking_redacted,
        )

    def _tool_result_prompt(self, msg: ToolResultMessage, attach_images: bool) -> Any:
        parts: list[str] = []
        images: list[Any] = []
        for item in msg.content:
            if isinstance(item, TextContent):
                parts.append(item.text)
            elif isinstance(item, ImageContent):
                if attach_images:
                    images.append(self._image_data(item))
                else:
                    parts.append("[Image omitted from history]")
        return self._make_prompt(
            _SOURCE_TOOL,
            "\n".join(parts) or "(no output)",
            tool_call_id=msg.tool_call_id,
            tool_result_is_error=bool(getattr(msg, "is_error", False)),
            images=images or None,
        )

    @staticmethod
    def _strip_schema_annotations(value: Any, property_names: bool = False) -> Any:
        if isinstance(value, list):
            return [DevinSDKProvider._strip_schema_annotations(v) for v in value]
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                kl = k.lower()
                if not property_names and (
                    kl in {"description", "title", "$comment"} or kl.startswith("x-")
                ):
                    continue
                if not property_names and kl in {
                    "const",
                    "default",
                    "enum",
                    "example",
                    "examples",
                }:
                    out[k] = v
                    continue
                out[k] = DevinSDKProvider._strip_schema_annotations(v, k == "properties")
            return out
        return value

    def _convert_tools(self, tools: list[ToolDefinition]) -> tuple[list[Any], str]:
        cls = dp.message_class("ChatToolDefinition")
        defs: list[Any] = []
        desc_blocks: list[str] = []
        for tool in tools:
            td = cls()
            td.name = tool.name
            # Devin uses the tool name as the wire description; real
            # descriptions are injected into the system prompt instead.
            td.description = tool.name
            td.json_schema_string = json.dumps(
                self._strip_schema_annotations(tool.parameters or {})
            )
            defs.append(td)
            desc = (tool.description or "").strip()
            if desc:
                desc_blocks.append(f'<tool name="{tool.name}">\n{desc}\n</tool>')
        section = ""
        if desc_blocks:
            section = "# tools descriptions\n" + "\n".join(desc_blocks)
        return defs, section

    def _build_request(
        self,
        messages: list[Message],
        system_prompt: str | None,
        tools: list[ToolDefinition] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> Any:
        req = dp.message_class("GetChatMessageRequest")()
        cfg = dp.message_class("CompletionConfiguration")()
        traj = dp.message_class("CortexTrajectoryReference")()

        tool_defs, tool_section = self._convert_tools(tools) if tools else ([], "")
        prompt_text = system_prompt or ""
        if tool_section:
            prompt_text = (
                prompt_text.rstrip("\r\n") + "\n\n" + tool_section
                if prompt_text.strip()
                else tool_section
            )

        req.metadata.CopyFrom(_make_metadata(self._token))
        req.prompt = prompt_text
        req.chat_model_uid = self.config.model
        req.request_type = _REQUEST_TYPE_CASCADE
        req.planner_mode = _PLANNER_MODE_DEFAULT
        req.cascade_id = str(uuid.uuid4())
        req.execution_id = str(uuid.uuid4())

        cfg.num_completions = 1
        cfg.max_tokens = max_tokens or self.config.max_tokens or 128000
        cfg.max_newlines = 400
        cfg.temperature = temperature if temperature is not None else 1.0
        cfg.top_k = 40
        cfg.top_p = 0.95
        req.configuration.CopyFrom(cfg)

        traj.trajectory_id = str(uuid.uuid4())
        traj.trajectory_type = _TRAJECTORY_TYPE_CASCADE
        traj.step_type = _STEP_TYPE_USER_INPUT
        req.trajectory_reference.CopyFrom(traj)

        req.chat_message_prompts.extend(self._convert_messages(messages))
        req.tools.extend(tool_defs)
        return req

    # -- streaming ----------------------------------------------------------

    async def _stream_impl(
        self,
        messages: list[Message],
        *,
        system_prompt: str | None = None,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking_level: str | None = None,
    ) -> LLMStream:
        request = self._build_request(messages, system_prompt, tools, temperature, max_tokens)
        llm_stream = LLMStream()
        llm_stream.set_iterator(
            self._process_stream(_stream_chat(request, self._token, self._base_url), llm_stream)
        )
        return llm_stream

    @staticmethod
    def _map_stop_reason(reason: int) -> StopReason:
        if reason in _STOP_TOOL_USE:
            return StopReason.TOOL_USE
        if reason in _STOP_MAX_TOKENS:
            return StopReason.LENGTH
        if reason in _STOP_ERROR:
            return StopReason.ERROR
        return StopReason.STOP

    async def _process_stream(
        self, responses: AsyncIterator[Any], llm_stream: LLMStream
    ) -> AsyncIterator[StreamPart]:
        tool_states: dict[int, dict[str, Any]] = {}
        next_tool_index = 0
        stop_reason = StopReason.STOP
        usage = Usage()
        sig_buf = ""

        async def flush_signature() -> AsyncIterator[StreamPart]:
            nonlocal sig_buf
            if sig_buf:
                yield ThinkPart(think="", signature=sig_buf)
                sig_buf = ""

        try:
            async for resp in responses:
                if resp.message_id:
                    llm_stream._id = resp.message_id
                u = resp.usage
                if u is not None:
                    usage = Usage(
                        input_tokens=int(u.input_tokens or 0),
                        output_tokens=int(u.output_tokens or 0),
                        cache_read_tokens=int(u.cache_read_tokens or 0),
                        cache_write_tokens=int(u.cache_write_tokens or 0),
                    )
                # Signature deltas are accumulated and emitted once as a
                # signature-only ThinkPart — matches how other providers
                # hand encrypted reasoning back for replay.
                if resp.delta_signature:
                    sig_buf += resp.delta_signature
                if resp.delta_thinking:
                    yield ThinkPart(think=resp.delta_thinking, signature=None)
                if resp.delta_text:
                    async for part in flush_signature():
                        yield part
                    yield TextPart(text=resp.delta_text)
                for delta in resp.delta_tool_calls:
                    async for part in flush_signature():
                        yield part
                    tc_id = delta.id or ""
                    idx = None
                    for i, st in tool_states.items():
                        if tc_id and st["id"] == tc_id:
                            idx = i
                            break
                    if idx is None and not tc_id and tool_states:
                        idx = max(tool_states)
                    if idx is None:
                        idx = next_tool_index
                        next_tool_index += 1
                        tool_states[idx] = {"id": tc_id, "name": "", "args": ""}
                        yield ToolCallStart(id=tc_id, name=delta.name or "", index=idx)
                    st = tool_states[idx]
                    if delta.name and not st["name"]:
                        st["name"] = delta.name
                    frag = delta.arguments_json or ""
                    if frag:
                        st["args"] += frag
                        yield ToolCallDelta(index=idx, arguments_delta=frag)
                reason = int(resp.stop_reason) if resp.stop_reason else 0
                if reason:
                    stop_reason = self._map_stop_reason(reason)
            llm_stream._usage = usage
            async for part in flush_signature():
                yield part
            for idx in sorted(tool_states):
                args = tool_states[idx]["args"]
                try:
                    parsed = json.loads(args) if args.strip() else {}
                    err = None
                except json.JSONDecodeError:
                    parsed = {"raw": args}
                    err = "invalid JSON"
                yield ToolCallEnd(index=idx, arguments=parsed, arguments_error=err)
            yield StreamDone(stop_reason=stop_reason)
        except Exception as e:
            yield StreamError(error=format_error(e))

    def should_retry_for_error(self, error: Exception) -> bool:
        from vtx.ai.rate_limit import is_rate_limit_error

        if is_rate_limit_error(error):
            return False
        if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):
            return True
        msg = str(error).lower()
        return any(
            s in msg
            for s in ("unavailable", "overloaded", "capacity", "deadline", "timeout", "http 5")
        )


__all__ = [
    "CLIENT_NAME",
    "CLIENT_VERSION",
    "DevinCredentials",
    "DevinSDKProvider",
    "get_devin_auth_path",
    "get_devin_native_path",
    "get_valid_devin_token_sync",
    "is_devin_logged_in",
    "list_devin_models",
    "load_devin_credentials",
    "login",
    "logout",
]
