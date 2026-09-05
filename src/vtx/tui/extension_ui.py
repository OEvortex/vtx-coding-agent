"""TUI-backed interactive UI primitives for extensions.

Implements :class:`vtx.ai.agent.extensions.ExtensionUIContext` on top of the
Textual app:

- ``await ctx.ui.confirm(title, message)`` — yes/no modal dialog
- ``await ctx.ui.select(title, options)``   — pick-one modal list
- ``await ctx.ui.input(title, placeholder)`` — free-text modal prompt
- ``ctx.ui.notify(message, level)``          — styled line in the chat log
- ``ctx.ui.setStatus(key, value)`` / ``ctx.ui.setWidget(key, lines)`` — a
  persistent footer bar rendered at the bottom of the screen for
  status/widgets

Dialogs are Textual ``ModalScreen`` subclasses pushed onto the running app;
the awaiting extension handler is resolved through an ``asyncio.Future``
wired to the screen's dismiss callback. ``timeout`` and ``signal`` kwargs
dismiss the dialog and return safe defaults.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from rich.text import Text
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static

from vtx.ai.agent.extensions import ExtensionUIContext

log = logging.getLogger("tui.extension_ui")

_NOTIFY_STYLES = {"info": "dim", "warning": "yellow", "error": "bold red"}


class ExtensionConfirmScreen(ModalScreen[bool]):
    BINDINGS: ClassVar[list] = [
        ("y", "yes", "Yes"),
        ("enter", "yes", "Yes"),
        ("n", "no", "No"),
        ("escape", "no", "No"),
    ]

    CSS = """
    ExtensionConfirmScreen {
        align: center middle;
    }
    #ext-confirm-box {
        width: 60;
        max-width: 90%;
        padding: 1 2;
        border: solid grey;
        background: $surface;
    }
    #ext-confirm-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #ext-confirm-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, title: str, message: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._message = message

    def compose(self):
        with Vertical(id="ext-confirm-box"):
            yield Label(Text(self._title, style="bold"), id="ext-confirm-title")
            if self._message:
                yield Static(self._message, id="ext-confirm-message")
            yield Label("[y]es / [n]o (Esc)", id="ext-confirm-hint")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class ExtensionSelectScreen(ModalScreen[str | None]):
    BINDINGS: ClassVar[list] = [("escape", "cancel", "Cancel")]

    CSS = """
    ExtensionSelectScreen {
        align: center middle;
    }
    #ext-select-box {
        width: 64;
        max-width: 90%;
        padding: 1 2;
        border: solid grey;
        background: $surface;
    }
    #ext-select-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #ext-select-list {
        width: 100%;
        max-height: 14;
    }
    """

    def __init__(self, title: str, options: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._options = options

    def compose(self):
        from textual.widgets.option_list import Option

        with Vertical(id="ext-select-box"):
            yield Label(self._title, id="ext-select-title")
            yield OptionList(
                *[Option(option, id=str(i)) for i, option in enumerate(self._options)],
                id="ext-select-list",
            )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        index = int(event.option_id) if event.option_id is not None else -1
        value = self._options[index] if 0 <= index < len(self._options) else None
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ExtensionInputScreen(ModalScreen[str | None]):
    BINDINGS: ClassVar[list] = [("escape", "cancel", "Cancel")]

    CSS = """
    ExtensionInputScreen {
        align: center middle;
    }
    #ext-input-box {
        width: 64;
        max-width: 90%;
        padding: 1 2;
        border: solid grey;
        background: $surface;
    }
    #ext-input-title {
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def __init__(
        self, title: str, placeholder: str = "", default: str | None = None, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._placeholder = placeholder
        self._default = default

    def compose(self):
        with Vertical(id="ext-input-box"):
            yield Label(self._title, id="ext-input-title")
            yield Input(
                value=self._default or "", placeholder=self._placeholder, id="ext-input-field"
            )

    def on_mount(self) -> None:
        self.query_one("#ext-input-field", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class _ExtensionWidgetBar(Static):
    """Bottom-of-screen bar rendering extension setStatus/setWidget state."""

    DEFAULT_CSS = """
    _ExtensionWidgetBar {
        dock: bottom;
        height: auto;
        max-height: 6;
        padding: 0 1;
        color: $text-muted;
        display: none;
    }
    """


class TextualExtensionUI(ExtensionUIContext):
    """Real modal-dialog implementation of the extension UI surface."""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._statuses: dict[str, str] = {}
        self._widgets: dict[str, list[str]] = {}

    # ---- dialog plumbing ---------------------------------------------------

    async def _run_dialog(
        self, screen_factory: Any, *, timeout: float | None, signal: Any, default: Any
    ) -> Any:
        """Push a modal screen and await its dismissal result."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()

        def on_dismiss(result: Any) -> None:
            if not future.done():
                future.set_result(result)

        screen = screen_factory()
        try:
            self._app.call_later(self._app.push_screen, screen, on_dismiss)
        except Exception:
            log.debug("failed to push extension dialog", exc_info=True)
            return default

        waiters = [asyncio.ensure_future(future)]
        if timeout is not None:
            waiters.append(asyncio.ensure_future(asyncio.sleep(timeout)))
        if signal is not None and hasattr(signal, "wait"):
            waiters.append(asyncio.ensure_future(signal.wait()))

        try:
            done, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            for waiter in waiters:
                waiter.cancel()
            self._safe_pop(screen)
            raise

        for waiter in pending:
            waiter.cancel()

        if future in done and not future.cancelled():
            return future.result()

        # Timed out or aborted: dismiss the dialog and return the default.
        self._safe_pop(screen)
        return default

    def _safe_pop(self, screen: Any) -> None:
        def _pop() -> None:
            try:
                if self._app.screen is screen:
                    self._app.pop_screen()
            except Exception:
                log.debug("failed to dismiss extension dialog", exc_info=True)

        try:
            self._app.call_later(_pop)
        except Exception:
            log.debug("failed to schedule dialog dismissal", exc_info=True)

    # ---- dialog primitives -------------------------------------------------

    async def select(
        self, title: str, options: list[str], *, timeout: float | None = None, signal: Any = None
    ) -> str | None:
        if not options:
            return None
        return await self._run_dialog(
            lambda: ExtensionSelectScreen(title, list(options)),
            timeout=timeout,
            signal=signal,
            default=None,
        )

    async def confirm(
        self,
        title: str,
        message: str | None = None,
        *,
        timeout: float | None = None,
        signal: Any = None,
    ) -> bool:
        result = await self._run_dialog(
            lambda: ExtensionConfirmScreen(title, message),
            timeout=timeout,
            signal=signal,
            default=False,
        )
        return bool(result)

    async def input(
        self,
        title: str,
        placeholder: str = "",
        *,
        default: str | None = None,
        timeout: float | None = None,
        signal: Any = None,
    ) -> str | None:
        return await self._run_dialog(
            lambda: ExtensionInputScreen(title, placeholder, default),
            timeout=timeout,
            signal=signal,
            default=default,
        )

    async def custom(
        self, component: Any, *, timeout: float | None = None, signal: Any = None
    ) -> Any:
        """Show a custom Textual component modally and await its result.

        ``component`` may be a pre-built widget, a widget class, a
        ``ModalScreen`` subclass/instance, or a zero-argument callable
        returning either. Screens are pushed directly; widgets are wrapped
        in :class:`ExtensionCustomScreen` (Escape dismisses with ``None``).
        """
        from textual.screen import ModalScreen
        from textual.widget import Widget

        def factory() -> ModalScreen[Any]:
            target = (
                component()
                if callable(component) and not isinstance(component, Widget)
                else component
            )
            if isinstance(target, ModalScreen):
                return target
            if isinstance(target, type) and issubclass(target, ModalScreen):
                return target()
            if isinstance(target, type) and issubclass(target, Widget):
                target = target()
            return ExtensionCustomScreen(target)

        return await self._run_dialog(factory, timeout=timeout, signal=signal, default=None)

    # ---- non-blocking surfaces ----------------------------------------------

    def notify(self, message: str, level: str = "info") -> None:
        style = _NOTIFY_STYLES.get(level, "dim")
        line = Static(Text(f"◆ {message}", style=style), classes="ext-notify")

        def _mount() -> None:
            try:
                chat_log = self._app.query_one("#chat-log")
                chat_log.mount(line)
                chat_log.scroll_end(animate=False)
            except Exception:
                log.debug("failed to render extension notification", exc_info=True)

        try:
            self._app.call_later(_mount)
        except Exception:
            log.debug("no active app for extension notification", exc_info=True)

    def setStatus(self, key: str, value: str | None) -> None:  # noqa: N802
        if value is None:
            self._statuses.pop(key, None)
        else:
            self._statuses[key] = value
        self._refresh_bar()
        self._sync_footer_statuses()

    def _sync_footer_statuses(self) -> None:
        try:
            from vtx.tui.widgets import InfoBar

            footer = self._app.query_one(InfoBar)
        except Exception:
            return
        footer.set_extension_statuses(self._statuses)

    def setWidget(  # noqa: N802
        self, key: str, widget: Any, options: dict[str, Any] | None = None
    ) -> None:
        lines: list[str]
        if widget is None:
            lines = []
        elif isinstance(widget, str):
            lines = widget.splitlines() or [""]
        elif isinstance(widget, (list, tuple)):
            lines = [str(item) for item in widget]
        else:
            lines = str(widget).splitlines() or [""]
        if lines:
            self._widgets[key] = lines
        else:
            self._widgets.pop(key, None)
        self._refresh_bar()

    # ---- widget-bar rendering ----------------------------------------------

    def _bar_content(self) -> Text:
        parts: list[Text] = []
        for key, value in self._statuses.items():
            parts.append(Text(f"{key}: {value}", style="cyan"))
        for lines in self._widgets.values():
            for line_text in lines:
                parts.append(Text(line_text))
        combined = Text()
        for i, part in enumerate(parts):
            if i:
                combined.append("\n")
            combined += part
        return combined

    def _refresh_bar(self) -> None:
        content = self._bar_content()

        def _update() -> None:
            try:
                bar = self._app.query_one(_ExtensionWidgetBar)
            except Exception:
                try:
                    bar = _ExtensionWidgetBar()
                    self._app.screen.mount(bar)
                except Exception:
                    log.debug("failed to mount extension widget bar", exc_info=True)
                    return
            try:
                bar.update(content)
                bar.display = bool(str(content))
            except Exception:
                log.debug("failed to update extension widget bar", exc_info=True)

        try:
            self._app.call_later(_update)
        except Exception:
            log.debug("no active app for extension widgets", exc_info=True)


class ExtensionCustomScreen(ModalScreen[Any]):
    """Modal wrapper that hosts an arbitrary extension-provided widget.

    The inner widget returns its result via ``self.screen.dismiss(value)``
    (the wrapper is its screen); Escape dismisses with ``None``.
    """

    BINDINGS: ClassVar[list] = [("escape", "cancel", "Close")]

    CSS = """
    ExtensionCustomScreen {
        align: center middle;
    }
    #ext-custom-box {
        width: auto;
        max-width: 90%;
        max-height: 80%;
        padding: 1 2;
        border: solid grey;
        background: $surface;
    }
    """

    def __init__(self, component: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._component = component

    def compose(self):
        with Vertical(id="ext-custom-box"):
            yield self._component

    def action_cancel(self) -> None:
        self.dismiss(None)
