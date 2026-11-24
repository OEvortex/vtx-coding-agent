"""Web tool: result parsing and fetch validation (HTTP mocked)."""

import pytest
from vx.tools import web
from vx.tools.web import WebParams, WebTool, _exa_text, _fetch


class TestExaText:
    def test_extracts_first_text(self):
        data = {"result": {"content": [{"text": "answer"}]}}
        assert _exa_text(data) == "answer"

    def test_empty_content_none(self):
        assert _exa_text({"result": {"content": []}}) is None
        assert _exa_text({}) is None


class TestFetch:
    def test_rejects_non_http(self):
        result = _fetch("ftp://x")
        assert not result.success
        assert "http" in (result.result or "")

    def test_jina_markdown_extraction(self, monkeypatch):
        monkeypatch.setattr(
            web, "_http", lambda *a, **k: (200, "Title\nMarkdown Content:\n# Hello\nbody")
        )
        result = _fetch("https://example.com")
        assert result.success
        assert "# Hello" in (result.result or "")
        assert "Markdown Content:" not in (result.result or "")

    def test_jina_http_error(self, monkeypatch):
        monkeypatch.setattr(web, "_http", lambda *a, **k: (503, "down"))
        result = _fetch("https://example.com")
        assert not result.success
        assert "503" in (result.result or "")

    def test_fetch_truncates_long_page(self, monkeypatch):
        monkeypatch.setattr(
            web,
            "_http",
            lambda *a, **k: (200, "Markdown Content:\n" + "x" * (web.MAX_FETCH_CHARS + 10)),
        )
        result = _fetch("https://example.com")
        assert result.success
        assert "truncated" in (result.result or "")


class TestExecute:
    @pytest.mark.asyncio
    async def test_search_empty_query_fails(self):
        result = await WebTool().execute(WebParams(query="   ", mode="search"))
        assert not result.success

    @pytest.mark.asyncio
    async def test_fetch_mode_dispatches(self, monkeypatch):
        seen = {}

        def fake_fetch(url):
            seen["url"] = url
            return web.ToolResult(success=True, result="ok")

        monkeypatch.setattr(web, "_fetch_jina", lambda url: ("text", None))
        result = await WebTool().execute(WebParams(query="https://x.com", mode="fetch"))
        assert result.success
