"""The session header's [Tools]/[Context]/[Skills] block must never double up.

Startup mounts the header once the runtime initializes, then again after MCP
connect adds tools. Mounting a second copy instead of replacing the first
prints the whole catalog twice at the top of every session.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from vtx.tui.chat import ChatLog
from vtx.tui.styles import get_styles


class _TestApp(App):
    CSS = get_styles()

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")


def _tools(count: int):
    from vtx.agent.tools.base import BaseTool

    class _T(BaseTool):
        def __init__(self, name: str):
            self.name = name
            self.description = ""
            self.parameters = {}
            self.read_only = False

        async def execute(self, **kwargs):
            return None

    return [_T(f"tool{i}") for i in range(count)]


@pytest.mark.asyncio
async def test_loaded_resources_header_is_replaced_not_duplicated():
    async with _TestApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)

        chat.add_loaded_resources(context_paths=[], skills=[], tools=_tools(3))
        assert len(list(chat.query(".loaded-resources"))) == 1

        chat.add_loaded_resources(context_paths=[], skills=[], tools=_tools(5))
        headers = list(chat.query(".loaded-resources"))
        assert len(headers) == 1, "second render must replace the first"
        assert "tool4" in headers[0].render().plain
