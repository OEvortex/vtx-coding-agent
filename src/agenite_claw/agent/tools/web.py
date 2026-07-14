"""Web search tool — delegating to the vtx Exa-based implementation."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from agenite_claw.agent.tools.base import Tool, tool_parameters
from agenite_claw.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from agenite_claw.config_base import Base
from vtx.tools.web import WebSearchTool as _VtxWebSearchTool

# Single source of truth for selectable search providers (CLI wizard + WebUI).
# "credential" describes what each provider needs: none / api_key / base_url /
# optional_api_key.
SEARCH_PROVIDER_OPTIONS: tuple[dict[str, str], ...] = (
    {"name": "duckduckgo", "label": "DuckDuckGo", "credential": "none"},
    {"name": "brave", "label": "Brave Search", "credential": "api_key"},
    {"name": "tavily", "label": "Tavily", "credential": "api_key"},
    {"name": "searxng", "label": "SearXNG", "credential": "base_url"},
    {"name": "jina", "label": "Jina", "credential": "api_key"},
    {"name": "kagi", "label": "Kagi", "credential": "api_key"},
    {"name": "exa", "label": "Exa", "credential": "api_key"},
    {"name": "olostep", "label": "Olostep", "credential": "api_key"},
    {"name": "bocha", "label": "Bocha", "credential": "api_key"},
    {"name": "volcengine", "label": "Volcengine Search", "credential": "api_key"},
    {"name": "keenable", "label": "Keenable", "credential": "optional_api_key"},
)


class WebSearchConfig(Base):
    """Web search configuration."""

    provider: str = "duckduckgo"
    api_key: str = ""
    base_url: str = ""
    max_results: int = 5
    timeout: int = 30


class WebToolsConfig(Base):
    """Web tools configuration."""

    enable: bool = True
    proxy: str | None = None
    user_agent: str | None = None
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


@tool_parameters(
    tool_parameters_schema(
        query=StringSchema("Search query"),
        count=IntegerSchema(1, description="Results (1-10)", minimum=1, maximum=10),
        timeRange=StringSchema(
            "Optional time filter (OneDay..OneYear, or YYYY-MM-DD..YYYY-MM-DD)"
        ),
        authLevel=IntegerSchema(
            0, description="Authority filter: 0=all, 1=authoritative", minimum=0, maximum=1
        ),
        queryRewrite=BooleanSchema(
            description="Provider-side query rewrite for ambiguous searches"
        ),
        required=["query"],
    )
)
class WebSearchTool(Tool):
    """Search the web using the vtx Exa-based web search tool."""

    _scopes = {"core", "subagent"}

    name = "web_search"
    description = (
        "Web search. Returns titles, URLs, snippets. count default 5 (max 10). "
        "Some providers support timeRange/authLevel/queryRewrite."
    )

    config_key = "web"

    @classmethod
    def config_cls(cls):
        return WebToolsConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.web.enable

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls()

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        query: str,
        count: int | None = None,
        time_range: str | None = None,
        auth_level: int | None = None,
        query_rewrite: bool | None = None,
        **kwargs: Any,
    ) -> str:
        n = min(max(count or 5, 1), 10)
        params = _VtxWebSearchTool.params(
            query=query, num_results=n, search_type="auto", livecrawl="fallback"
        )
        result = await _VtxWebSearchTool().execute(params)
        return result.result or ""
