"""The launch screen: centered masthead, hints sharing the wordmark's rows.

The full catalog used to print under the header and push the prompt off a short
terminal, so the header now carries the wordmark plus the counts line, and the
lists live behind /skills and /tools.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from vtx.agent.context.skills import Skill
from vtx.agent.tools.base import BaseTool
from vtx.core.paths import get_agents_dir
from vtx.protocol.types import ToolResult
from vtx.tui.brand import VTX_LOGO
from vtx.tui.chat import ChatLog
from vtx.tui.commands.catalog import skill_sections, tool_label, tool_sections
from vtx.tui.styles import get_styles


class _TestApp(App):
    CSS = get_styles()

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")


def _render_plain(label) -> str:
    """Plain text of the label's visual, no ANSI and no Textual wrapper."""
    strips = label.visual.render_strips(100, 20, label.styles, label.app.console.options)
    return "\n".join("".join(segment.text for segment in strip) for strip in strips)


def _tool(name: str, server: str | None = None, tool: str | None = None) -> BaseTool:
    """A stand-in tool: only its name (and MCP server, for the label) is read."""

    class Stub(BaseTool):
        async def execute(self, params, cancel_event=None) -> ToolResult:
            return ToolResult(success=True, result="ok")

    stub = Stub()
    stub.name = name
    if server and tool:
        stub.server = server  # ty:ignore[unresolved-attribute]
        stub.tool_name = tool  # ty:ignore[unresolved-attribute]
    return stub


def _skill(name: str, category: str, path: str, *, bundled: bool = False) -> Skill:
    return Skill(name=name, description=path, path=path, category=category, bundled=bundled)


@pytest.mark.asyncio
async def test_header_is_centered_with_hints_beside_the_wordmark():
    async with _TestApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        chat.add_session_info("v1.2.2", empty_state=True)
        await pilot.pause()

        rows = _render_plain(pilot.app.query_one(".session-info", Label)).split("\n")

        # The wordmark's six rows, widened by the hint cells that share them,
        # then a blank and the empty-state hint line.
        assert len(rows) == 8, f"wordmark rows + blank + hint expected:\n{rows}"

        # The masthead is centered as a block in the 100-column terminal: its
        # left edge leaves as little room as its right edge. The wordmark's own
        # letter shapes add a space or two inside those edges, so the left edge
        # is the smallest indent any row starts at.
        content = [row for row in rows if row.strip()]
        left = min(len(row) - len(row.lstrip()) for row in content)
        width = max(len(row.rstrip()) for row in content) - left
        assert 2 * left + width in range(99, 101), (
            f"left edge {left} does not center a {width}-wide masthead:\n{rows}"
        )

        # The wordmark keeps its own column on all six rows, and the version
        # rides the first one rather than restating the name in text.
        for index in range(6):
            assert rows[index].lstrip().startswith(VTX_LOGO[index].strip()), f"\n{rows}"
        assert "v1.2.2" in rows[0]
        assert "VTX" not in "".join(rows), "the logo spells it; the title must not repeat it"

        # Hints share the wordmark's rows instead of stacking under it: no key
        # is repeated, so none is stated with two meanings.
        hint_rows = rows[1:4]
        for key in ("commands", "files", "history", "interrupt", "newline", "exit"):
            assert key in "".join(hint_rows), f"{key!r} missing from hints:\n{rows}"
        # No key is stated twice, so none carries two meanings.
        assert "↑/↓" in "".join(hint_rows)
        assert hint_rows[0].count("/") <= 3, f"a key repeated:\n{rows}"

        # Empty state only on a fresh session.
        assert any("Try:" in row for row in rows), f"empty-state hint missing:\n{rows}"


@pytest.mark.asyncio
async def test_header_omits_empty_state_hint_when_resuming():
    async with _TestApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        chat.add_session_info("v1.2.2")
        await pilot.pause()

        rows = _render_plain(pilot.app.query_one(".session-info", Label)).split("\n")
        assert not any("Try:" in row for row in rows)
        assert len(rows) == 6, f"wordmark rows only, no hint line:\n{rows}"


@pytest.mark.asyncio
async def test_loaded_resources_summary_replaces_rather_than_stacks():
    async with _TestApp().run_test() as pilot:
        chat = pilot.app.query_one("#chat-log", ChatLog)
        context_paths = ["/repo/AGENTS.md"]
        skills = [_skill(f"skill{i}", "general", f"/repo/skill{i}/SKILL.md") for i in range(52)]
        tools = [_tool(f"tool{i}") for i in range(14)]

        chat.add_loaded_resources(context_paths, skills, tools)
        assert len(list(chat.query(".loaded-resources"))) == 1

        # MCP connect re-renders with a bigger tool surface.
        tools = [*tools, _tool("mcp__ddg-search__web_search", "ddg-search", "web_search")]
        chat.add_loaded_resources(context_paths, skills, tools)
        labels = list(chat.query(".loaded-resources"))
        assert len(labels) == 1, "second render must replace the first"

        summary = _render_plain(labels[0])
        assert "1 context file (AGENTS.md)" in summary
        assert "52 skills" in summary
        assert "15 tools" in summary
        assert "/tools" in summary and "/skills" in summary
        # The lists are gone from the launch screen.
        assert "skill0" not in summary and "tool0" not in summary


def test_skill_sections_group_by_category_and_mark_local():
    global_root = get_agents_dir() / "skills"
    local = _skill("zebra", "review", "/repo/.agents/skills/z/SKILL.md")
    bundled = _skill("bundle", "review", str(global_root / "bundle" / "SKILL.md"), bundled=True)
    glob = _skill("alpha", "review", str(global_root / "alpha" / "SKILL.md"))

    sections = skill_sections([local, glob, bundled])
    assert sections == [("review (3)", [("alpha", ""), ("bundle", ""), ("zebra", "(local)")])]


def test_tool_sections_split_builtin_from_mcp_servers():
    builtin = _tool("read")
    mcp = _tool("mcp__ddg-search__web_search", "ddg-search", "web_search")
    sections = tool_sections([builtin, mcp])
    assert sections == [
        ("built-in (1)", [("read", "")]),
        ("ddg-search (1)", [("ddg-search > web_search", "")]),
    ]


def test_tool_label_unwraps_mcp_wire_name():
    assert tool_label(_tool("bash")) == "bash"
    assert tool_label(_tool("mcp__x__y", "ddg-search", "web_search")) == "ddg-search > web_search"
