"""Tests for extension UI primitives (pi ctx.ui parity).

Covers:

- ``ExtensionUIContext`` no-op defaults (headless/print mode)
- ``EventBus`` context injection: two-arg handlers unchanged, three-arg
  handlers receive a ``HandlerContext`` with the installed UI
- ``TextualExtensionUI`` dialog resolution via a fake app (push/dismiss,
  timeout defaulting) without booting a real Textual app
"""

from __future__ import annotations

import asyncio

import pytest

from vtx.ai.agent.extensions import TOOL_CALL as EVENT_TOOL_CALL
from vtx.ai.agent.extensions import ExtensionUIContext, HandlerContext
from vtx.tui.extension_ui import ExtensionConfirmScreen, TextualExtensionUI

# =============================================================================
# Base no-ops (headless mode)
# =============================================================================


@pytest.mark.asyncio
async def test_noop_ui_defaults():
    ui = ExtensionUIContext()
    assert await ui.confirm("title") is False
    assert await ui.select("title", ["a"]) is None
    assert await ui.input("title") is None


def test_noop_ui_non_blocking_surfaces():
    ui = ExtensionUIContext()
    ui.notify("hi")
    ui.setStatus("k", "v")
    ui.setWidget("k", ["line"])
    ui.setWorkingMessage("working")


# =============================================================================
# EventBus context injection
# =============================================================================


def _make_bus():
    from vtx.ai.agent.extensions import EventBus

    return EventBus()


@pytest.mark.asyncio
async def test_two_arg_handler_unchanged():
    bus = _make_bus()
    seen = {}

    def handler(event, payload):
        seen["args"] = (event, payload)

    bus.on(EVENT_TOOL_CALL, handler)
    await bus.emit(EVENT_TOOL_CALL, name="bash")
    assert seen["args"][0] == EVENT_TOOL_CALL
    assert seen["args"][1]["name"] == "bash"


@pytest.mark.asyncio
async def test_three_arg_handler_receives_context_with_default_ui():
    bus = _make_bus()
    seen = {}

    async def handler(event, payload, ctx):
        seen["ctx"] = ctx

    bus.on(EVENT_TOOL_CALL, handler)
    await bus.emit(EVENT_TOOL_CALL)
    ctx = seen["ctx"]
    assert isinstance(ctx, HandlerContext)
    assert isinstance(ctx.ui, ExtensionUIContext)
    assert ctx.mode == "print"
    # No-op defaults apply without an installed host UI.
    assert await ctx.ui.confirm("x") is False


@pytest.mark.asyncio
async def test_three_arg_handler_receives_installed_ui():
    bus = _make_bus()

    class FakeUI(ExtensionUIContext):
        pass

    fake = FakeUI()
    bus.set_ui_context(fake, cwd="/tmp/proj", mode="tui")
    seen = {}

    async def handler(event, payload, ctx):
        seen["ctx"] = ctx

    bus.on(EVENT_TOOL_CALL, handler)
    await bus.emit(EVENT_TOOL_CALL)
    ctx: HandlerContext = seen["ctx"]
    assert ctx.ui is fake
    assert ctx.cwd == "/tmp/proj"
    assert ctx.mode == "tui"


@pytest.mark.asyncio
async def test_var_positional_handler_gets_context():
    bus = _make_bus()
    seen = {}

    def handler(*args):
        seen["argc"] = len(args)

    bus.on(EVENT_TOOL_CALL, handler)
    await bus.emit(EVENT_TOOL_CALL)
    assert seen["argc"] == 3


@pytest.mark.asyncio
async def test_sync_emit_passes_context_to_three_arg_handler():
    bus = _make_bus()
    seen = {}

    def handler(event, payload, ctx):
        seen["ctx"] = ctx

    bus.on("session_start", handler)
    bus.emit_sync("session_start", cwd="/x")
    assert isinstance(seen["ctx"], HandlerContext)


# =============================================================================
# TextualExtensionUI with a fake app
# =============================================================================


class FakeApp:
    """Records call_later callbacks; push_screen stores the dismiss callback."""

    def __init__(self):
        self.callbacks: list = []
        self.pushed: list = []
        self.dismiss_callbacks: list = []

    def call_later(self, fn, *args):
        self.callbacks.append((fn, args))

    def push_screen(self, screen, callback=None):
        self.pushed.append(screen)
        self.dismiss_callbacks.append(callback)

    @property
    def screen(self):  # used by _safe_pop
        return self.pushed[-1] if self.pushed else None

    def pop_screen(self):
        if self.pushed:
            self.pushed.pop()
            self.dismiss_callbacks.pop()

    def flush(self):
        """Run all pending call_later callbacks once."""
        pending, self.callbacks = self.callbacks, []
        for fn, args in pending:
            fn(*args)

    def dismiss_last(self, value):
        callback = self.dismiss_callbacks[-1]
        if callback is not None:
            callback(value)


@pytest.mark.asyncio
async def test_confirm_resolves_true_on_dismiss():
    app = FakeApp()
    ui = TextualExtensionUI(app)
    task = asyncio.ensure_future(ui.confirm("Delete files?", "this cannot be undone"))
    await asyncio.sleep(0)
    app.flush()  # runs push_screen

    assert len(app.pushed) == 1
    assert isinstance(app.pushed[0], ExtensionConfirmScreen)

    app.dismiss_last(True)
    assert await task is True


@pytest.mark.asyncio
async def test_confirm_resolves_false_on_dismiss_false():
    app = FakeApp()
    ui = TextualExtensionUI(app)
    task = asyncio.ensure_future(ui.confirm("Sure?"))
    await asyncio.sleep(0)
    app.flush()
    app.dismiss_last(False)
    assert await task is False


@pytest.mark.asyncio
async def test_select_returns_chosen_option():
    from vtx.tui.extension_ui import ExtensionSelectScreen

    app = FakeApp()
    ui = TextualExtensionUI(app)
    task = asyncio.ensure_future(ui.select("Pick", ["a", "b"]))
    await asyncio.sleep(0)
    app.flush()
    assert isinstance(app.pushed[0], ExtensionSelectScreen)
    app.dismiss_last("b")
    assert await task == "b"


@pytest.mark.asyncio
async def test_input_returns_text():
    from vtx.tui.extension_ui import ExtensionInputScreen

    app = FakeApp()
    ui = TextualExtensionUI(app)
    task = asyncio.ensure_future(ui.input("Name", placeholder="who"))
    await asyncio.sleep(0)
    app.flush()
    assert isinstance(app.pushed[0], ExtensionInputScreen)
    app.dismiss_last("vortex")
    assert await task == "vortex"


@pytest.mark.asyncio
async def test_dialog_timeout_returns_safe_default():
    app = FakeApp()
    ui = TextualExtensionUI(app)
    task = asyncio.ensure_future(ui.confirm("Slow?", timeout=0.01))
    await asyncio.sleep(0)
    app.flush()  # push
    result = await task
    assert result is False
    app.flush()  # scheduled pop_screen runs
    assert app.pushed == []


@pytest.mark.asyncio
async def test_empty_select_short_circuits():
    app = FakeApp()
    ui = TextualExtensionUI(app)
    assert await ui.select("Nothing", []) is None
    assert app.pushed == []


@pytest.mark.asyncio
async def test_signal_aborts_dialog():
    app = FakeApp()
    ui = TextualExtensionUI(app)
    abort = asyncio.Event()
    task = asyncio.ensure_future(ui.select("Pick", ["a"], signal=abort))
    await asyncio.sleep(0)
    app.flush()
    abort.set()
    assert await task is None


def test_status_and_widget_state_updates():
    app = FakeApp()
    ui = TextualExtensionUI(app)
    ui.setStatus("fetch", "running")
    ui.setWidget("todo", ["- a", "- b"])
    content = ui._bar_content()
    text = str(content)
    assert "fetch: running" in text
    assert "- a" in text and "- b" in text
    ui.setStatus("fetch", None)  # clearing an unknown key must not raise
    ui.setWidget("todo", None)
    assert "todo" not in ui._widgets


# =============================================================================
# ctx.ui.custom
# =============================================================================


@pytest.mark.asyncio
async def test_noop_custom_returns_none():
    ui = ExtensionUIContext()
    assert await ui.custom(object()) is None


@pytest.mark.asyncio
async def test_custom_wraps_widget_instance_and_returns_result():
    from textual.widget import Widget

    from vtx.tui.extension_ui import ExtensionCustomScreen

    app = FakeApp()
    ui = TextualExtensionUI(app)
    task = asyncio.ensure_future(ui.custom(Widget()))
    await asyncio.sleep(0)
    app.flush()

    assert len(app.pushed) == 1
    assert isinstance(app.pushed[0], ExtensionCustomScreen)

    app.dismiss_last({"picked": "x"})
    assert await task == {"picked": "x"}


@pytest.mark.asyncio
async def test_custom_widget_class_is_instantiated_and_wrapped():
    from textual.widget import Widget

    from vtx.tui.extension_ui import ExtensionCustomScreen

    class MyWidget(Widget):
        pass

    app = FakeApp()
    ui = TextualExtensionUI(app)
    task = asyncio.ensure_future(ui.custom(MyWidget))
    await asyncio.sleep(0)
    app.flush()

    screen = app.pushed[0]
    assert isinstance(screen, ExtensionCustomScreen)
    assert isinstance(screen._component, MyWidget)

    app.dismiss_last("done")
    assert await task == "done"


@pytest.mark.asyncio
async def test_custom_screen_subclass_pushed_directly():
    from textual.screen import ModalScreen

    class MyScreen(ModalScreen[str]):
        pass

    app = FakeApp()
    ui = TextualExtensionUI(app)
    task = asyncio.ensure_future(ui.custom(MyScreen))
    await asyncio.sleep(0)
    app.flush()

    # The extension's screen is pushed as-is, no wrapper.
    assert isinstance(app.pushed[0], MyScreen)

    app.dismiss_last("result")
    assert await task == "result"


@pytest.mark.asyncio
async def test_custom_callable_factory_used():
    from textual.widget import Widget

    from vtx.tui.extension_ui import ExtensionCustomScreen

    def make():
        return Widget()

    app = FakeApp()
    ui = TextualExtensionUI(app)
    task = asyncio.ensure_future(ui.custom(make))
    await asyncio.sleep(0)
    app.flush()

    assert isinstance(app.pushed[0], ExtensionCustomScreen)
    app.dismiss_last(None)
    assert await task is None
