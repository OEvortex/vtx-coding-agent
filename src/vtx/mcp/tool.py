"""Adapt an MCP tool into a vtx :class:`BaseTool`.

The result flows through the same path as every other tool: the permission
gate reads :attr:`McpTool.mutating`, the agent loop validates against
``params``, the TUI renders the block. Nothing downstream needs to know the
call left the process.

Two annotations matter for behaviour rather than display. ``readOnlyHint``
makes the tool non-mutating, so it skips the approval prompt. ``destructiveHint``
does the opposite, forcing one even under a permissive mode.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from vtx.ai.agent.tools.base import BaseTool
from vtx.ai.agent.tools.schema import (
    MAX_TOOL_NAME_LENGTH,
    json_schema_to_pydantic,
    normalize_object_schema,
    sanitize_tool_name,
)
from vtx.core.bytes_util import format_bytes
from vtx.core.types import ToolResult

from .client import McpClient, McpRequestOptions
from .content import split_content, to_tool_content
from .types import CallToolResult, Tool as McpToolDef

log = logging.getLogger("mcp.tool")

MCP_OUTPUT_MAX_BYTES = 20 * 1024
"""Model-facing text beyond this is cut, with the full text written to a file.

An MCP tool can return anything -- a filesystem read, a database dump -- and the
session-level tool-result budget in ``context_governance`` is 200K, which is too
generous to be the only limit for a third-party tool. A pointer to a file the
model can read is strictly more useful than the first 20K of a large payload.
"""


def create_mcp_tool_name(server: str, tool: str, is_taken=None) -> str:
    """``mcp__<server>__<tool>``, sanitized for provider tool-name limits.

    Providers accept at most 64 characters of ``[A-Za-z0-9_-]``. Sanitizing can
    also collide (``a.b`` and ``a_b`` both become ``a_b``), so a name that is
    already taken or too long gets a hash suffix derived from the *original*
    server and tool, which keeps it unique rather than merely shorter.
    """
    base = sanitize_tool_name(f"mcp__{server}__{tool}")
    taken = is_taken(base) if is_taken is not None else False
    if len(base) <= MAX_TOOL_NAME_LENGTH and not taken:
        return base
    digest = hashlib.sha256(f"{server}\0{tool}".encode()).hexdigest()[:8]
    prefix = base[: MAX_TOOL_NAME_LENGTH - len(digest) - 1]
    candidate = f"{prefix}_{digest}"
    if is_taken is not None:
        suffix = 1
        unique = candidate
        while is_taken(unique):
            extra = hashlib.sha256(f"{server}\0{tool}\0{suffix}".encode()).hexdigest()[:8]
            unique = f"{base[: MAX_TOOL_NAME_LENGTH - len(extra) - 1]}_{extra}"
            suffix += 1
        candidate = unique
    return candidate


def _is_text_mime_type(mime_type: str | None) -> bool:
    if not mime_type:
        return False
    kind = mime_type.split(";", 1)[0].strip().lower()
    return kind.startswith("text/") or kind in ("application/json",) or kind.endswith(
        ("+json", "+xml")
    )


def _extension_of(uri: str) -> str:
    path = urlparse(uri).path or uri
    tail = path.rsplit("/", 1)[-1]
    _, _, ext = tail.rpartition(".")
    return f".{ext}" if ext and 1 <= len(ext) <= 8 and ext.isalnum() else ".bin"


def save_output_file(data: bytes, extension: str) -> Path:
    """Write truncated output or a binary blob to a private temp file.

    Mode 0600: the payload can be source code, credentials, or customer data
    from whatever server the user configured, so it is theirs alone to read.
    """
    handle, name = tempfile.mkstemp(prefix="vtx-mcp-", suffix=extension)
    path = Path(name)
    try:
        with open(handle, "wb") as f:
            f.write(data)
        path.chmod(0o600)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def truncate_middle(text: str, max_bytes: int) -> tuple[str, bool]:
    """Keep the start and the end of ``text``, cutting the middle.

    The head of a tool result says what it is; the tail usually holds the
    answer. A plain prefix cut throws the answer away.
    """
    encoded = text.encode("utf-8", "replace")
    if len(encoded) <= max_bytes:
        return text, False
    head = max_bytes // 2
    tail = max_bytes - head
    return (
        encoded[:head].decode("utf-8", "replace")
        + f"\n\n[... {len(encoded) - max_bytes} bytes elided ...]\n\n"
        + encoded[-tail:].decode("utf-8", "replace")
    ), True


def convert_mcp_result(result: CallToolResult) -> ToolResult:
    """Flatten an MCP result into the ``ToolResult`` vtx tools return."""
    content = to_tool_content(result)
    text, images = split_content(content)
    truncated_text, truncated = truncate_middle(text, MCP_OUTPUT_MAX_BYTES)

    if truncated:
        try:
            path = save_output_file(text.encode("utf-8", "replace"), ".txt")
            where = f"[Full output: {path} (read it with offset/limit)]"
        except OSError as exc:
            where = f"[Could not save the full output: {exc}]"
        truncated_text = (
            f"Warning: truncated output ({format_bytes(len(text.encode('utf-8', 'replace')))} total)\n\n"
            f"{truncated_text}\n\n{where}"
        )

    is_error = result.get("isError") is True
    if is_error and not truncated_text and not images:
        truncated_text = "MCP tool reported an error with no output."

    return ToolResult(
        success=not is_error,
        result=truncated_text or None,
        images=images or None,
        ui_summary="[red]error[/red]" if is_error else None,
    )


def _resource_link_text(block: dict[str, Any]) -> str:
    details = [block.get("mimeType")]
    size = block.get("size")
    if isinstance(size, int):
        details.append(format_bytes(size))
    described = ", ".join(d for d in details if d)
    suffix = f" ({described})" if described else ""
    title = block.get("title") or block.get("name") or block.get("uri")
    return f"[Resource {block.get('uri')} \"{title}\"{suffix}]"


def enrich_result(result: CallToolResult) -> CallToolResult:
    """Replace opaque resource placeholders with something actionable.

    ``to_tool_content`` renders an audio or binary block as ``[... omitted]``
    because a model cannot consume it. But a *text* resource can be, and a
    binary one can be written to disk for the model to read. Both are strictly
    more useful than dropping them.
    """
    blocks = result.get("content")
    if not isinstance(blocks, list):
        return result
    enriched: list[Any] = []
    changed = False
    for block in blocks:
        if not isinstance(block, dict):
            enriched.append(block)
            continue
        if block.get("type") == "resource_link":
            enriched.append({"type": "text", "text": _resource_link_text(block)})
            changed = True
            continue
        if block.get("type") != "resource":
            enriched.append(block)
            continue
        resource = block.get("resource")
        if not isinstance(resource, dict) or not isinstance(resource.get("blob"), str):
            enriched.append(block)
            continue
        mime_type = resource.get("mimeType")
        try:
            data = base64.b64decode(resource["blob"], validate=False)
        except (ValueError, TypeError):
            # Not base64 after all; leave the block for to_tool_content to
            # render as a placeholder rather than guessing at the bytes.
            enriched.append(block)
            continue
        if _is_text_mime_type(mime_type):
            enriched.append({"type": "text", "text": data.decode("utf-8", "replace")})
            changed = True
            continue
        if isinstance(mime_type, str) and mime_type.startswith("image/"):
            # Left alone on purpose: to_tool_content turns an embedded image
            # resource into real image content the model can actually see.
            # Writing it to a file and naming the path would be worse.
            enriched.append(block)
            continue
        kind = mime_type or "unknown type"
        try:
            path = save_output_file(data, _extension_of(str(resource.get("uri", ""))))
        except OSError as exc:
            enriched.append(
                {
                    "type": "text",
                    "text": f"[Binary resource {resource.get('uri')} ({kind}) could not be saved: {exc}]",
                }
            )
        else:
            enriched.append(
                {
                    "type": "text",
                    "text": f"[Binary resource {resource.get('uri')} ({kind}, "
                    f"{format_bytes(len(data))}) saved to {path}]",
                }
            )
        changed = True
    if not changed:
        return result
    return {**result, "content": enriched}


@dataclass
class McpToolCaller:
    """The slice of a connection :class:`McpTool` needs."""

    server_name: str
    call: Any
    """``async (tool_name, arguments, options) -> CallToolResult``."""


class McpTool(BaseTool):
    """One MCP tool, exposed to the model like any other vtx tool."""

    def __init__(
        self,
        *,
        server: str,
        definition: McpToolDef,
        name: str,
        caller: McpToolCaller,
        timeout_ms: int = 60_000,
    ) -> None:
        self._server = server
        self._definition = definition
        self._caller = caller
        self._timeout_ms = timeout_ms

        self.name = name
        self.description = (definition.get("description") or "").strip() or (
            f"MCP tool {definition.get('name')} from server {server}"
        )
        annotations = definition.get("annotations") or {}
        self.tool_icon = "⇄"
        self.prompt_guidelines = ()

        schema = normalize_object_schema(definition.get("inputSchema") or {})
        self.params = json_schema_to_pydantic(name, schema)

        # readOnlyHint is the server telling us the tool cannot change the
        # world, so the permission gate should not interrupt the user for it.
        # destructiveHint does the opposite and is respected even if the tool
        # forgot to also set readOnlyHint.
        read_only = annotations.get("readOnlyHint") is True
        destructive = annotations.get("destructiveHint") is True
        self.mutating = not read_only or destructive

    @property
    def server(self) -> str:
        return self._server

    @property
    def tool_name(self) -> str:
        return str(self._definition.get("name", ""))

    def format_call(self, params: Any) -> str:
        data = params.model_dump(exclude_none=True) if hasattr(params, "model_dump") else {}
        if not data:
            return f"{self._server}/{self.tool_name}"
        parts = []
        for key, value in data.items():
            rendered = str(value)
            if len(rendered) > 80:
                rendered = rendered[:77] + "..."
            parts.append(f"{key}={rendered}")
        return f"{self._server}/{self.tool_name} " + " / ".join(parts)

    async def execute(
        self,
        params: Any,
        cancel_event: asyncio.Event | None = None,
        on_output: Any = None,
    ) -> ToolResult:
        arguments = params.model_dump(exclude_none=True) if hasattr(params, "model_dump") else {}

        progress_updates: list[tuple[str, dict[str, Any]]] = []
        options = McpRequestOptions(
            cancel_event=cancel_event,
            timeout_ms=self._timeout_ms,
        )
        if on_output is not None:
            options.on_progress = lambda progress: progress_updates.append(
                (progress.get("message") or f"progress {progress.get('progress')}", progress)
            )

        try:
            result = await self._caller.call(self.tool_name, arguments, options)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return ToolResult(success=False, result=f"MCP tool {self._server}/{self.tool_name} failed: {exc}")

        if progress_updates and on_output is not None:
            # Report the last progress line, not all of them: the block shows
            # one live status and a queue of history would just be noise.
            message, _payload = progress_updates[-1]
            on_output(message)

        return convert_mcp_result(enrich_result(result))


__all__ = [
    "MCP_OUTPUT_MAX_BYTES",
    "McpTool",
    "McpToolCaller",
    "convert_mcp_result",
    "create_mcp_tool_name",
    "enrich_result",
    "save_output_file",
    "truncate_middle",
]
