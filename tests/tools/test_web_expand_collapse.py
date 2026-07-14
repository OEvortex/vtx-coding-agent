"""Tests for the web search tool expand/collapse behavior.

The expand/collapse affordance (ctrl+o) requires the tool to populate
``ui_details`` (collapsed body) and ``ui_details_full`` (expanded body) on
its :class:`ToolResult`. The web tool used to only set ``ui_summary``,
which kept the body hidden. These tests pin the fix.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from vtx.tools.web import SearchParams, WebSearchTool


def _sse_payload(text: str) -> str:
    body = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"text": text}]}}
    return f"data: {json.dumps(body)}"


def _mock_response(text: str):
    response = AsyncMock()
    response.text = _sse_payload(text)
    response.raise_for_status = lambda: None
    response.json = lambda: {"result": {"content": [{"text": text}]}}
    return response


@pytest.mark.asyncio
async def test_web_search_long_result_exposes_expand_hint():
    tool = WebSearchTool()
    long_text = "\n".join(f"Result line {i}" for i in range(20))

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=_mock_response(long_text)
        )
        result = await tool.execute(SearchParams(query="python"))

    assert result.success is True
    assert result.ui_summary is not None
    # Collapsed body should be present and carry the expand hint.
    assert result.ui_details is not None
    assert "ctrl+o" in result.ui_details
    assert result.ui_details_full == long_text


@pytest.mark.asyncio
async def test_web_search_short_result_has_no_full_view():
    tool = WebSearchTool()
    short_text = "Single line of search output."

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=_mock_response(short_text)
        )
        result = await tool.execute(SearchParams(query="python"))

    assert result.success is True
    assert result.ui_details == short_text
    # No truncation needed, so the "expanded" view is the same as collapsed.
    assert result.ui_details_full is None
