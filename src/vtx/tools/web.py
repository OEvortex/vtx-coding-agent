"""Web fetch and web search tools for vtx."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import httpx
from pydantic import BaseModel, Field

from vtx.ui.tool_output import escape_tool_output_text, truncate_tool_output_text

from ..core.types import ToolResult
from .base import BaseTool

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_FETCH_TIMEOUT = 25.0


def _split_for_expand(text: str) -> tuple[str, str | None]:
    """Split long text into collapsed + expanded form so ctrl+o can reveal it.

    Mirrors the fallback in ``SessionUIMixin._format_tool_result_text`` so the
    web tools get the same expand/collapse affordance as bash/edit.
    """
    collapsed, truncated = truncate_tool_output_text(text)
    return collapsed, escape_tool_output_text(text) if truncated else None


# ===========================================================================
# WebFetchTool  (Exa MCP — no API key needed)
# ===========================================================================


class FetchParams(BaseModel):
    urls: Annotated[list[str], Field(min_length=1, description="URLs to read (batch in one call)")]
    max_characters: int = Field(default=3000, ge=1, description="Max chars per page")


class WebFetchTool(BaseTool):
    """Read a webpage's full content as clean markdown via the Exa MCP endpoint."""

    name = "fetch_webpage"
    tool_icon = "🌐"
    params = FetchParams
    mutating = False
    description = "Read a webpage as clean markdown (Exa). Batch URLs. Not for JS-rendered pages."
    prompt_guidelines = ()

    _MCP_URL = "https://mcp.exa.ai/mcp"

    def format_call(self, params: FetchParams) -> str:
        if len(params.urls) == 1:
            return params.urls[0]
        return f"{params.urls[0]} (+{len(params.urls) - 1} more)"

    async def execute(
        self, params: FetchParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "web_fetch_exa",
                "arguments": {"urls": params.urls, "maxCharacters": params.max_characters},
            },
        }
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
                resp = await client.post(self._MCP_URL, headers=headers, json=payload)
                resp.raise_for_status()

            for line in resp.text.splitlines():
                if not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                text = self._extract_text(data)
                if text:
                    ui_details, ui_details_full = _split_for_expand(text)
                    return ToolResult(
                        success=True,
                        result=text,
                        ui_summary="[dim]exa[/dim]",
                        ui_details=ui_details,
                        ui_details_full=ui_details_full,
                    )

            try:
                text = self._extract_text(resp.json())
                if text:
                    ui_details, ui_details_full = _split_for_expand(text)
                    return ToolResult(
                        success=True,
                        result=text,
                        ui_summary="[dim]exa[/dim]",
                        ui_details=ui_details,
                        ui_details_full=ui_details_full,
                    )
            except Exception:
                pass

            return ToolResult(
                success=False,
                result="No results returned from Exa API",
                ui_summary="[red]No Exa results[/red]",
            )

        except httpx.TimeoutException:
            msg = f"Exa timed out after {_FETCH_TIMEOUT}s"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")
        except httpx.HTTPStatusError as e:
            msg = f"Exa API error {e.response.status_code}"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")
        except Exception as e:
            msg = f"Exa fetch failed: {e}"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

    @staticmethod
    def _extract_text(data: dict) -> str | None:
        content = data.get("result", {}).get("content", [])
        if content and isinstance(content, list):
            return content[0].get("text")
        return None


# ===========================================================================
# WebSearchTool  (Exa neural search — no API key needed)
# ===========================================================================


class SearchParams(BaseModel):
    query: str = Field(description="Search query")
    num_results: int = Field(default=8, ge=1, le=20, description="Number of results")
    search_type: str = Field(default="auto", description="'auto', 'neural', or 'keyword'")
    livecrawl: str = Field(default="fallback", description="'fallback', 'always', or 'never'")


class WebSearchTool(BaseTool):
    """Web search via Exa MCP endpoint (no API key required)."""

    name = "web_search"
    tool_icon = "🔍"
    params = SearchParams
    mutating = False
    description = "Web search (Exa neural). Returns titles, URLs, snippets. Needs internet."
    prompt_guidelines = ()

    _MCP_URL = "https://mcp.exa.ai/mcp"
    _TIMEOUT = 25.0

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
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
                resp = await client.post(self._MCP_URL, headers=headers, json=payload)
                resp.raise_for_status()

            # Try SSE lines first
            for line in resp.text.splitlines():
                if not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                text = self._extract_text(data)
                if text:
                    ui_details, ui_details_full = _split_for_expand(text)
                    return ToolResult(
                        success=True,
                        result=text,
                        ui_summary=f"[dim]exa: {params.query!r}[/dim]",
                        ui_details=ui_details,
                        ui_details_full=ui_details_full,
                    )

            # Fallback: parse whole body as JSON
            try:
                text = self._extract_text(resp.json())
                if text:
                    ui_details, ui_details_full = _split_for_expand(text)
                    return ToolResult(
                        success=True,
                        result=text,
                        ui_summary=f"[dim]exa: {params.query!r}[/dim]",
                        ui_details=ui_details,
                        ui_details_full=ui_details_full,
                    )
            except Exception:
                pass

            return ToolResult(
                success=False,
                result="No results returned from Exa API",
                ui_summary="[red]No Exa results[/red]",
            )

        except httpx.TimeoutException:
            msg = f"Exa timed out after {self._TIMEOUT}s"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")
        except httpx.HTTPStatusError as e:
            msg = f"Exa API error {e.response.status_code}"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")
        except Exception as e:
            msg = f"Exa search failed: {e}"
            return ToolResult(success=False, result=msg, ui_summary=f"[red]{msg}[/red]")

    @staticmethod
    def _extract_text(data: dict) -> str | None:
        content = data.get("result", {}).get("content", [])
        if content and isinstance(content, list):
            return content[0].get("text")
        return None
