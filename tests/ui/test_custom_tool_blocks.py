"""Tests for the custom TUI block API: extensions and agents can ship
a custom :class:`vtx.ui.blocks.ToolBlock` subclass for a tool they
register.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from vtx.agents.api import AgentAPI
from vtx.agents.loader import load_agent
from vtx.extensions import Extension, ExtensionAPI, ExtensionTool
from vtx.tools.base import BaseTool
from vtx.ui.blocks import ToolBlock
from vtx.ui.chat import ChatLog
from vtx.ui.styles import get_styles

# ---------------------------------------------------------------------------
# A small custom block, used as a fixture
# ---------------------------------------------------------------------------


CUSTOM_BLOCK_INSTANCES: list[CustomToolBlock] = []


class CustomToolBlock(ToolBlock):
    """A minimal custom block that records its own construction and the
    tool it was bound to. Used by the tests below to assert that the
    chat log instantiates this class instead of the default
    :class:`ToolBlock` when ``tool.ui_block`` is set.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        CUSTOM_BLOCK_INSTANCES.append(self)
        self.set_result_calls: list[tuple[str | None, str | None, bool]] = []

    def set_result(  # type: ignore[override]
        self,
        ui_summary: str | None,
        ui_details: str | None,
        success: bool,
        markup: bool = True,
        ui_details_full: str | None = None,
        images: list | None = None,
    ) -> None:
        self.set_result_calls.append((ui_summary, ui_details, success))
        # Delegate the actual rendering to the base implementation so
        # the user can still see the result in the chat log.
        super().set_result(
            ui_summary,
            ui_details,
            success,
            markup=markup,
            ui_details_full=ui_details_full,
            images=images,
        )


# ---------------------------------------------------------------------------
# ExtensionTool: ui_block attribute
# ---------------------------------------------------------------------------


class TestExtensionToolUiBlock:
    def test_default_is_none(self):
        tool = ExtensionTool(
            name="t",
            description="d",
            parameters={"type": "object", "properties": {}},
            params_model=_noop_params(),
            execute_fn=lambda args, ctx: None,
            owner="ext",
            mutating=False,
            label="t",
        )
        assert tool.ui_block is None

    def test_ui_block_stored(self):
        tool = ExtensionTool(
            name="t",
            description="d",
            parameters={"type": "object", "properties": {}},
            params_model=_noop_params(),
            execute_fn=lambda args, ctx: None,
            owner="ext",
            mutating=False,
            label="t",
            ui_block=CustomToolBlock,
        )
        assert tool.ui_block is CustomToolBlock


def _noop_params():
    """Build a minimal valid params model for ExtensionTool tests."""
    from pydantic import create_model

    return create_model("NoopParams", input=(str | None, None))


# ---------------------------------------------------------------------------
# ExtensionAPI.register_tool: ui_block kwarg
# ---------------------------------------------------------------------------


def _make_ext_api(name: str = "ext_test") -> tuple[ExtensionAPI, Extension]:
    """Build a minimal ``ExtensionAPI`` wired to a real :class:`Extension`."""
    from vtx.extensions import EventBus

    ext = Extension(name=name, path=Path(f"/{name}.py"))
    api = ExtensionAPI(ext, bus=EventBus(), cwd=".", session_file=None, config_dir=Path("/"))
    return api, ext


class TestRegisterToolUiBlock:
    def test_register_tool_accepts_ui_block(self):
        api, ext = _make_ext_api()
        tool = api.register_tool(
            name="greet",
            description="Say hi",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            execute=lambda args, ctx: {"success": True, "result": f"hi {args['name']}"},
            mutating=False,
            ui_block=CustomToolBlock,
        )
        assert tool.ui_block is CustomToolBlock
        assert ext.tools["greet"].ui_block is CustomToolBlock

    def test_register_tool_default_ui_block_is_none(self):
        api, _ext = _make_ext_api()
        tool = api.register_tool(
            name="greet",
            description="Say hi",
            parameters={"type": "object", "properties": {}},
            execute=lambda args, ctx: {"success": True, "result": "hi"},
        )
        assert tool.ui_block is None

    def test_register_local_tool_accepts_ui_block(self):
        api, ext = _make_ext_api()
        tool = api.register_local_tool(
            agent="reviewer",
            name="greet",
            description="Say hi",
            parameters={"type": "object", "properties": {}},
            execute=lambda args, ctx: {"success": True, "result": "hi"},
            mutating=False,
            ui_block=CustomToolBlock,
        )
        assert tool.ui_block is CustomToolBlock
        assert ext.local_tools["reviewer"]["greet"].ui_block is CustomToolBlock


# ---------------------------------------------------------------------------
# AgentAPI.local_tool: ui_block kwarg
# ---------------------------------------------------------------------------


class TestLocalToolUiBlock:
    def _make_agent_api(self, tmp_path: Path) -> AgentAPI:
        agent_file = tmp_path / "reviewer.py"
        agent_file.write_text(
            "from vtx.agents import AgentDef\nAGENT = AgentDef(name='reviewer', description='x')\n"
        )
        loaded = load_agent(agent_file, cwd=str(tmp_path), config_dir=tmp_path)
        return AgentAPI(loaded, cwd=str(tmp_path), config_dir=tmp_path)

    def test_local_tool_accepts_ui_block(self, tmp_path):
        api = self._make_agent_api(tmp_path)
        tool = api.local_tool(
            name="custom",
            description="custom",
            parameters={"type": "object", "properties": {}},
            execute=lambda args, ctx: {"success": True, "result": "ok"},
            mutating=False,
            ui_block=CustomToolBlock,
        )
        assert tool.ui_block is CustomToolBlock
        assert api._loaded.local_tools["custom"].ui_block is CustomToolBlock

    def test_local_tool_decorator_accepts_ui_block(self, tmp_path):
        api = self._make_agent_api(tmp_path)

        @api.local_tool(
            name="custom",
            description="custom",
            parameters={"type": "object", "properties": {}},
            mutating=False,
            ui_block=CustomToolBlock,
        )
        def custom(args, ctx):
            return {"success": True, "result": "ok"}

        assert api._loaded.local_tools["custom"].ui_block is CustomToolBlock


# ---------------------------------------------------------------------------
# ChatLog.start_tool: ui_block instantiation
# ---------------------------------------------------------------------------


class _TestApp(App):
    CSS = get_styles()

    def compose(self) -> ComposeResult:
        yield ChatLog(id="chat-log")


class TestChatLogStartTool:
    @pytest.mark.asyncio
    async def test_default_block_when_no_tool(self):
        async with _TestApp().run_test() as pilot:
            chat = pilot.app.query_one("#chat-log", ChatLog)
            CUSTOM_BLOCK_INSTANCES.clear()
            block = chat.start_tool("some_tool", "id1", "call_msg", icon="→")
            assert isinstance(block, ToolBlock)
            assert not isinstance(block, CustomToolBlock)
            assert block.tool is None

    @pytest.mark.asyncio
    async def test_custom_block_when_tool_has_ui_block(self):
        async with _TestApp().run_test() as pilot:
            chat = pilot.app.query_one("#chat-log", ChatLog)
            CUSTOM_BLOCK_INSTANCES.clear()
            tool = _build_tool_with_custom_block("fancy_tool", CustomToolBlock)
            block = chat.start_tool("fancy_tool", "id2", "call_msg", icon="→", tool=tool)
            assert isinstance(block, CustomToolBlock)
            # The chat log sets ``block.tool`` after construction so the
            # custom block can introspect the bound BaseTool.
            assert block.tool is tool
            assert len(CUSTOM_BLOCK_INSTANCES) == 1

    @pytest.mark.asyncio
    async def test_default_block_when_tool_has_no_ui_block(self):
        async with _TestApp().run_test() as pilot:
            chat = pilot.app.query_one("#chat-log", ChatLog)
            CUSTOM_BLOCK_INSTANCES.clear()
            tool = _build_tool_with_custom_block("plain_tool", None)
            block = chat.start_tool("plain_tool", "id3", "call_msg", icon="→", tool=tool)
            assert isinstance(block, ToolBlock)
            assert not isinstance(block, CustomToolBlock)
            assert block.tool is tool
            assert len(CUSTOM_BLOCK_INSTANCES) == 0


def _build_tool_with_custom_block(name: str, ui_block: type | None) -> BaseTool:
    """Build an ExtensionTool with a given ``ui_block``."""
    return ExtensionTool(
        name=name,
        description="d",
        parameters={"type": "object", "properties": {}},
        params_model=_noop_params(),
        execute_fn=lambda args, ctx: None,
        owner="ext",
        mutating=False,
        label=name,
        ui_block=ui_block,
    )


# ---------------------------------------------------------------------------
# Custom block: inherits ToolBlock's hooks so set_result still works
# ---------------------------------------------------------------------------


class TestCustomBlockRendering:
    @pytest.mark.asyncio
    async def test_set_result_is_routed(self):
        async with _TestApp().run_test() as pilot:
            chat = pilot.app.query_one("#chat-log", ChatLog)
            CUSTOM_BLOCK_INSTANCES.clear()
            tool = _build_tool_with_custom_block("fancy", CustomToolBlock)
            block = chat.start_tool("fancy", "id", "call", icon="→", tool=tool)
            assert isinstance(block, CustomToolBlock)
            # Let the block mount and run its compose() before we set
            # the result, otherwise super().set_result() can't find
            # the #tool-output label it wants to update.
            await pilot.pause()
            chat.set_tool_result("id", "summary", "details", True)
            await pilot.pause()
            assert block.set_result_calls == [("summary", "details", True)]


# ---------------------------------------------------------------------------
# BaseTool: ui_block is a class attribute with a default of None
# ---------------------------------------------------------------------------


class TestBaseToolUiBlockDefault:
    def test_default_is_none(self):
        assert BaseTool.ui_block is None
