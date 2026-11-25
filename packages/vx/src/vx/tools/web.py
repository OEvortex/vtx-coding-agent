"""Web tool for vx: one tool, two modes — search the web or fetch a URL.

``mode="search"`` (default) asks Exa's keyless MCP endpoint; ``mode="fetch"``
reads a URL through Jina Reader's markdown view. Both jobs share one schema
because the model asks for them with one intent ("look this up" vs "read this
page") and one schema costs less context than two.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Literal

from ctx.agent.tools.base import BaseTool
from ctx.protocol.types import ToolResult
from pydantic import BaseModel, Field

#: Ceiling on the text a fetch returns, so one page cannot flood the context.
MAX_FETCH_CHARS = 30_000
MAX_RESULTS = 10
_TIMEOUT = 25

_EXA_URL = "https://mcp.exa.ai/mcp"
_JINA_URL = "https://r.jina.ai/"

#: A browser UA: both endpoints answer a bare client with a bot page.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class WebParams(BaseModel):
    query: str = Field(description="Search query, or the URL to download when mode='fetch'")
    mode: Literal["search", "fetch"] = Field(
        default="search",
        description="'search' (default) queries the web; 'fetch' downloads a URL's text",
    )


class WebTool(BaseTool):
    name = "web"
    tool_icon = "🌐"
    params = WebParams
    mutating = False
    parallel_safe = True
    description = (
        "Web access. mode='search' (default): query the web, returns titles, URLs and "
        "snippets. mode='fetch': download a URL and return its readable text."
    )

    def format_call(self, params: WebParams) -> str:
        return f"{params.mode}: {params.query[:100]}"

    async def execute(
        self, params: WebParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        if params.mode == "fetch":
            return await asyncio.to_thread(_fetch, params.query.strip())
        return await asyncio.to_thread(_search, params.query.strip())


# -- http --------------------------------------------------------------------


def _http(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = _TIMEOUT,
) -> tuple[int, str]:
    request = urllib.request.Request(
        url, data=data, headers={"User-Agent": _USER_AGENT, **(headers or {})}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def _error(text: str) -> ToolResult:
    return ToolResult(success=False, result=text, ui_summary=f"[red]{text}[/red]")


# -- search ------------------------------------------------------------------


def _search(query: str) -> ToolResult:
    if not query:
        return _error("empty search query")
    text, error = _search_exa(query)
    if text:
        return ToolResult(success=True, result=text, ui_summary=f"[dim]exa: {query[:40]}[/dim]")
    if error:
        return _error(error)
    return ToolResult(success=True, result="No results found", ui_summary="[dim]No results[/dim]")


def _search_exa(query: str) -> tuple[str | None, str | None]:
    """Exa's public MCP endpoint: keyless search over the web."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "web_search_exa",
            "arguments": {
                "query": query,
                "type": "auto",
                "numResults": MAX_RESULTS,
                "livecrawl": "fallback",
            },
        },
    }
    try:
        _, body = _http(
            _EXA_URL,
            data=json.dumps(payload).encode(),
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
    except Exception as exc:
        return None, f"Exa request failed: {exc}"

    # Streamed responses arrive as SSE frames; a plain body is JSON.
    candidates = [line[6:] for line in body.splitlines() if line.startswith("data: ")]
    candidates.append(body)
    for chunk in candidates:
        try:
            text = _exa_text(json.loads(chunk))
        except json.JSONDecodeError:
            continue
        if text:
            return text, None
    return None, "No results returned from Exa"


def _exa_text(data: dict) -> str | None:
    content = data.get("result", {}).get("content", [])
    if isinstance(content, list) and content:
        text = content[0].get("text")
        return text if isinstance(text, str) and text.strip() else None
    return None


# -- fetch -------------------------------------------------------------------


def _fetch(url: str) -> ToolResult:
    if not url.startswith(("http://", "https://")):
        return _error("mode='fetch' requires an http(s) URL")

    text, error = _fetch_jina(url)
    if text is None:
        return _error(error or f"Fetch failed: {url}")

    if len(text) > MAX_FETCH_CHARS:
        text = text[:MAX_FETCH_CHARS] + f"\n\n[truncated at {MAX_FETCH_CHARS} chars]"
    return ToolResult(success=True, result=text, ui_summary=f"[dim]fetch: {url[:70]}[/dim]")


def _fetch_jina(url: str) -> tuple[str | None, str | None]:
    """Jina Reader renders a page as markdown, which reads far better than HTML."""
    try:
        status, body = _http(f"{_JINA_URL}{url}", headers={"Accept": "text/plain"})
    except Exception as exc:
        return None, f"Fetch failed: {exc}"
    if status != 200:
        return None, f"Fetch failed: Jina Reader HTTP {status}"
    marker = body.find("Markdown Content:")
    text = (body[marker + len("Markdown Content:") :] if marker != -1 else body).strip()
    if not text or text[:200].lower().lstrip().startswith(("<!doctype", "<html")):
        return None, "Fetch returned no readable content"
    return text, None


__all__ = ["WebParams", "WebTool"]
