"""Web search tool for vtx via the Exa MCP endpoint (no API key needed).

Re-exports the harness-native ``web`` tool from :mod:`vtx.ai.agent.tools.web`.
"""

from __future__ import annotations

from vtx.ai.agent.tools.web import SearchParams, WebSearchTool, WebTool

__all__ = ["SearchParams", "WebSearchTool", "WebTool"]
