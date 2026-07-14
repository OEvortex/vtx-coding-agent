"""Web search tool for vtx via the Exa MCP endpoint (no API key needed)."""

from __future__ import annotations

import asyncio
import json

import httpx
from pydantic import BaseModel, Field

from vtx.ui.tool_output import escape_tool_output_text, truncate_tool_output_text

from ..core.types import ToolResult
from .base import BaseTool

# ---------------------------------------------------------------------------
# Shared constants / helpers
# ---------------------------------------------------------------------------

_MCP_URL = "https://mcp.exa.ai/mcp"
_TIMEOUT = 25.0


def _split_for_expand(text: str) -> tuple[str, str | None]:
    """Split long text into collapsed + expanded form so ctrl+o can reveal it.

    Mirrors the fallback in ``SessionUIMixin._format_tool_result_text`` so the
    web tool gets the same expand/collapse affordance as bash/edit.
    """
    collapsed, truncated = truncate_tool_output_text(text)
    return collapsed, escape_tool_output_text(text) if truncated else None


def _extract_text(data: dict) -> str | None:
    content = data.get("result", {}).get("content", [])
    if content and isinstance(content, list):
        return content[0].get("text")
    return None


async def _call_exa(payload: dict) -> tuple[str | None, str | None]:
    """POST a JSON-RPC call to the Exa MCP endpoint.

    Returns ``(text, None)`` on success or ``(None, error_message)`` on any
    failure (timeout, HTTP error, empty body). Handles both SSE-streamed and
    plain-JSON responses.
    """
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


# ===========================================================================
# WebTool  (Exa neural search — no API key needed)
# ===========================================================================


class SearchParams(BaseModel):
    query: str = Field(description="Search query")
    num_results: int = Field(default=8, ge=1, le=20, description="Number of results")
    search_type: str = Field(default="auto", description="'auto', 'neural', or 'keyword'")
    livecrawl: str = Field(default="fallback", description="'fallback', 'always', or 'never'")


class WebTool(BaseTool):
    """Web search via Exa MCP endpoint (no API key required)."""

    name = "web"
    tool_icon = "🔍"
    params = SearchParams
    mutating = False
    description = "Web search (Exa neural). Returns titles, URLs, snippets. Needs internet."
    prompt_guidelines = ()

    def format_call(self, params: SearchParams) -> str:
        return f'"{params.query}"'

    async def execute(
        self, params: SearchParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "web_search_exa",
                "arguments": {
                    "query": params.query,
                    "type": params.search_type,
                    "numResults": params.num_results,
                    "livecrawl": params.livecrawl,
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


# ===========================================================================
# Backward-compatible alias (used by agenite_claw and legacy tests)
# ===========================================================================


class WebSearchTool(WebTool):
    """Web search via Exa MCP endpoint (no API key required)."""

    name = "web_search"
    tool_icon = "🔍"
    params = SearchParams
    description = "Web search (Exa neural). Returns titles, URLs, snippets. Needs internet."
