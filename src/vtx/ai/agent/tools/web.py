from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from vtx.ai.agent.tools.base import BaseTool
from vtx.core.types import ToolResult

MAX_SEARCH_RESULTS = 10
EXA_SEARCH_ENDPOINT = "https://api.exa.ai/search"
DEFAULT_FETCH_TEXT_LIMIT = 5000


class SearchParams(BaseModel):
    query: str = Field(min_length=1, description="Search query string")
    max_results: int = Field(
        default=5,
        ge=1,
        le=MAX_SEARCH_RESULTS,
        description="Maximum number of search results to return (1-10)",
    )
    fetch_content: bool = Field(
        default=False,
        description="Whether to fetch full text content for the top results",
    )


class WebTool(BaseTool[SearchParams]):
    name = "web"
    params = SearchParams
    tool_icon = "🌐"
    mutating = False

    description = (
        "Search the web using Exa neural search. Returns titles, URLs, and snippets. "
        "Can optionally fetch page text contents. Requires EXA_API_KEY."
    )

    prompt_guidelines = (
        (
            "Use `web` to find current documentation, library APIs, blog posts, "
            "or real-time info outside the codebase."
        ),
    )

    def format_call(self, params: SearchParams) -> str:
        return params.query

    async def execute(
        self,
        params: SearchParams,
        cancel_event: asyncio.Event | None = None,
        tool_call_id: str | None = None,
    ) -> ToolResult:
        api_key = os.environ.get("EXA_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                result=(
                    "EXA_API_KEY environment variable is not set. "
                    "Web search requires an Exa API key."
                ),
                ui_summary="missing EXA_API_KEY",
            )

        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
        }

        payload: dict[str, Any] = {
            "query": params.query,
            "numResults": params.max_results,
            "type": "neural",
            "useAutoprompt": True,
        }

        if params.fetch_content:
            payload["contents"] = {
                "text": {"maxCharacters": DEFAULT_FETCH_TEXT_LIMIT},
            }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(EXA_SEARCH_ENDPOINT, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()

            results = data.get("results", [])
            if not results:
                return ToolResult(
                    success=True,
                    result=f"No web results found for query: {params.query!r}",
                    ui_summary="no results",
                )

            formatted_results: list[str] = []
            for i, item in enumerate(results, 1):
                title = item.get("title") or "Untitled"
                url = item.get("url") or ""
                text = item.get("text", "").strip()

                entry = [f"### {i}. {title}", f"URL: {url}"]
                if text:
                    cleaned_text = re.sub(r"\s+", " ", text)[:DEFAULT_FETCH_TEXT_LIMIT]
                    entry.append(f"Content:\n{cleaned_text}")
                elif "highlights" in item:
                    highlights = "\n".join(f"- {h}" for h in item.get("highlights", []))
                    entry.append(f"Highlights:\n{highlights}")

                formatted_results.append("\n".join(entry))

            ui_summary = f"{len(results)} results for {params.query!r}"
            return ToolResult(
                success=True,
                result="\n\n---\n\n".join(formatted_results),
                ui_summary=ui_summary,
            )

        except httpx.HTTPStatusError as e:
            return ToolResult(
                success=False,
                result=(
                    f"Exa search failed with status {e.response.status_code}: {e.response.text}"
                ),
                ui_summary=f"HTTP {e.response.status_code}",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                result=f"Web search error: {e}",
                ui_summary="error",
            )


# Alias for backward compatibility
WebSearchTool = WebTool

__all__ = [
    "DEFAULT_FETCH_TEXT_LIMIT",
    "EXA_SEARCH_ENDPOINT",
    "MAX_SEARCH_RESULTS",
    "SearchParams",
    "WebSearchTool",
    "WebTool",
]

