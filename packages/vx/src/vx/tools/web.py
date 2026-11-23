"""Web tools for vx — fetch URLs and search the web."""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.request

from ctx.agent.tools.base import BaseTool
from ctx.protocol.types import ToolResult
from pydantic import BaseModel, Field


class WebParams(BaseModel):
    url: str = Field(description="URL to fetch")
    extract_depth: str = Field(
        description='Extraction depth: "basic" or "advanced"', default="basic"
    )


class WebTool(BaseTool):
    name = "web"
    tool_icon = "🌐"
    params = WebParams
    mutating = False
    parallel_safe = True
    description = (
        "Fetch and extract text content from a URL. "
        "Use for reading web pages, documentation, or API responses."
    )

    def format_call(self, params: WebParams) -> str:
        return params.url[:100]

    async def execute(
        self, params: WebParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        url = params.url
        if not url.startswith(("http://", "https://")):
            return ToolResult(
                success=False,
                result="URL must start with http:// or https://",
                ui_summary="[red]Invalid URL[/red]",
            )

        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; vx-agent/1.0)",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                content_type = response.headers.get("Content-Type", "")
                raw = response.read()

            if "text/html" in content_type or "application/xhtml" in content_type:
                # Basic HTML to text extraction
                text = _html_to_text(raw.decode("utf-8", errors="replace"))
            elif "application/json" in content_type:
                try:
                    text = json.dumps(json.loads(raw), indent=2)
                except json.JSONDecodeError:
                    text = raw.decode("utf-8", errors="replace")
            else:
                text = raw.decode("utf-8", errors="replace")

            if len(text) > 50000:
                text = text[:50000] + "\n\n[output truncated]"

            return ToolResult(
                success=True, result=text, ui_summary=f"[dim]Fetched {url[:60]}[/dim]"
            )
        except urllib.error.HTTPError as e:
            return ToolResult(
                success=False,
                result=f"HTTP {e.code}: {e.reason}",
                ui_summary=f"[red]HTTP {e.code}[/red]",
            )
        except urllib.error.URLError as e:
            return ToolResult(
                success=False,
                result=f"Failed to fetch: {e.reason}",
                ui_summary="[red]Fetch failed[/red]",
            )
        except Exception as e:
            return ToolResult(success=False, result=f"Error: {e}", ui_summary="[red]Error[/red]")


def _html_to_text(html: str) -> str:
    """Very basic HTML to text extraction."""
    # Remove script and style tags
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Replace common tags
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    # Clean up whitespace
    text = re.sub(r"\n\s*\n", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return text.strip()


class WebSearchParams(BaseModel):
    query: str = Field(description="Search query")


class WebSearchTool(BaseTool):
    name = "web_search"
    tool_icon = "🔎"
    params = WebSearchParams
    mutating = False
    parallel_safe = True
    description = (
        "Search the web for current information. Returns snippets and URLs from relevant pages."
    )

    def format_call(self, params: WebSearchParams) -> str:
        return f'search: "{params.query}"'

    async def execute(
        self, params: WebSearchParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        """Search using DuckDuckGo HTML (no API key needed)."""
        import urllib.parse

        query = params.query
        try:
            search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            req = urllib.request.Request(
                search_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; vx-agent/1.0)",
                    "Accept": "text/html",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode("utf-8", errors="replace")

            # Parse results from DuckDuckGo HTML
            results = _parse_duckduckgo_results(html)

            if not results:
                return ToolResult(
                    success=True, result="No results found", ui_summary="[dim]No results[/dim]"
                )

            lines: list[str] = []
            for i, r in enumerate(results[:10], 1):
                lines.append(f"{i}. {r['title']}")
                lines.append(f"   {r['url']}")
                if r.get("snippet"):
                    lines.append(f"   {r['snippet']}")
                lines.append("")

            return ToolResult(
                success=True,
                result="\n".join(lines).strip(),
                ui_summary=f"[dim]{len(results)} results[/dim]",
            )
        except Exception as e:
            return ToolResult(
                success=False, result=f"Search failed: {e}", ui_summary="[red]Search failed[/red]"
            )


def _parse_duckduckgo_results(html: str) -> list[dict]:
    """Parse DuckDuckGo HTML search results."""
    results: list[dict] = []
    # Find result containers
    pattern = r'<a class="result__a" href="(.*?)".*?title="(.*?)".*?>\s*(.*?)\s*</a>'
    for match in re.finditer(pattern, html, re.DOTALL):
        url_raw = match.group(1)
        title = match.group(2).strip()
        body_match = re.search(
            r'<a.*?class="result__snippet".*?>(.*?)</a>', html[match.end() :], re.DOTALL
        )
        snippet = ""
        if body_match:
            snippet = re.sub(r"<[^>]+>", "", body_match.group(1)).strip()

        # Convert DDG URL format
        if url_raw.startswith("//links.duckduckgo.com/"):
            continue  # Skip redirect URLs

        if title:
            results.append({"title": title, "url": url_raw, "snippet": snippet})

    return results
