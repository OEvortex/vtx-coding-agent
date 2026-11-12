"""Web search + URL fetch tool for vtx (Exa MCP search, httpx fetch)."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from vtx.agent.tools.base import BaseTool
from vtx.protocol.types import ToolResult

_MCP_URL = "https://mcp.exa.ai/mcp"
_TIMEOUT = 25.0


def _split_for_expand(text: str) -> tuple[str, str | None]:
    try:
        from vtx.tui.tool_output import escape_tool_output_text, truncate_tool_output_text

        collapsed, truncated = truncate_tool_output_text(text)
        return collapsed, escape_tool_output_text(text) if truncated else None
    except Exception:
        return text, None


def _extract_text(data: dict) -> str | None:
    content = data.get("result", {}).get("content", [])
    if content and isinstance(content, list):
        return content[0].get("text")
    return None


async def _call_exa(payload: dict) -> tuple[str | None, str | None]:
    """POST a JSON-RPC call to the Exa MCP endpoint."""
    import httpx

    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_MCP_URL, headers=headers, json=payload)
            resp.raise_for_status()
    except httpx.TimeoutException:
        return None, f"Exa timed out after {_TIMEOUT}s"
    except httpx.HTTPStatusError as e:
        return None, f"Exa API error {e.response.status_code}"
    except Exception as e:
        return None, f"Exa request failed: {e}"

    # SSE-streamed response: find the first data line with content.
    for line in resp.text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            data = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        text = _extract_text(data)
        if text:
            return text, None

    # Fallback: parse the whole body as a single JSON object.
    try:
        text = _extract_text(resp.json())
        if text:
            return text, None
    except Exception:
        pass

    return None, "No results returned from Exa API"


_MAX_FETCH_CHARS = 30_000

_JINA_HEADER_RE = re.compile(
    r"^(Title|URL Source|Published Time|Warning|Markdown Content):.*\n?", re.MULTILINE
)


def _strip_jina_header(text: str) -> str:
    """Drop Jina Reader's metadata envelope, keep the markdown body."""
    marker = text.find("Markdown Content:")
    if marker != -1:
        return text[marker + len("Markdown Content:") :].strip()
    return _JINA_HEADER_RE.sub("", text).strip()


async def _fetch_url(url: str) -> tuple[str | None, str | None]:
    """Fetch a URL as markdown via Jina Reader (r.jina.ai)."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"https://r.jina.ai/{url}")
    except httpx.TimeoutException:
        return None, f"Fetch timed out after {_TIMEOUT}s"
    except Exception as e:
        return None, f"Fetch failed: {e}"

    if resp.status_code != 200:
        return None, f"Fetch failed: Jina Reader HTTP {resp.status_code}"

    text = _strip_jina_header(resp.text)
    if not text or "<html" in text[:200].lower():
        return None, "Fetch returned no readable content"
    if len(text) > _MAX_FETCH_CHARS:
        text = text[:_MAX_FETCH_CHARS] + f"\n[truncated at {_MAX_FETCH_CHARS} chars]"
    return text, None


class SearchParams(BaseModel):
    query: str = Field(description="Search query, or a URL when mode='fetch'")
    mode: Literal["search", "fetch"] = Field(
        default="search",
        description="'search' (default) queries Exa; 'fetch' downloads a URL's text",
    )


class WebTool(BaseTool):
    """Web search via Exa MCP endpoint (no API key required)."""

    name = "web"
    tool_icon = "🔍"
    params = SearchParams
    mutating = False
    parallel_safe = True
    description = (
        "Web search (Exa) or URL fetch. mode='search' (default): query the web, "
        "returns titles/URLs/snippets. mode='fetch': download a URL's readable text. "
        "Needs internet."
    )
    prompt_guidelines = ()

    def format_call(self, params: SearchParams) -> str:
        return f"{params.mode}: {params.query}"

    async def execute(
        self, params: SearchParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        if params.mode == "fetch":
            url = params.query.strip()
            if not url.startswith(("http://", "https://")):
                msg = "mode='fetch' requires an http(s) URL"
                return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")
            text, err = await _fetch_url(url)
            if err:
                return ToolResult(success=False, result=err, ui_summary=f"[red]{err}[/red]")
            assert text is not None
            ui_details, ui_details_full = _split_for_expand(text)
            return ToolResult(
                success=True,
                result=text,
                ui_summary=f"[dim]fetch: {url}[/dim]",
                ui_details=ui_details,
                ui_details_full=ui_details_full,
            )

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "web_search_exa",
                "arguments": {
                    "query": params.query,
                    "type": "auto",
                    "numResults": 8,
                    "livecrawl": "fallback",
                },
            },
        }
        text, err = await _call_exa(payload)
        if err:
            return ToolResult(success=False, result=err, ui_summary=f"[red]{err}[/red]")
        assert text is not None
        ui_details, ui_details_full = _split_for_expand(text)
        return ToolResult(
            success=True,
            result=text,
            ui_summary=f"[dim]exa: {params.query!r}[/dim]",
            ui_details=ui_details,
            ui_details_full=ui_details_full,
        )


class WebSearchTool(WebTool):
    """Web search via Exa MCP endpoint (no API key required)."""

    name = "web_search"
    tool_icon = "🔍"
    params = SearchParams
    description = (
        "Web search (Exa) or URL fetch. mode='search' (default); mode='fetch' "
        "downloads a URL's text. Needs internet."
    )


__all__ = ["SearchParams", "WebSearchTool", "WebTool"]
