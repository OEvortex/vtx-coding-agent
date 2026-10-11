"""``ctx.ui`` — the interactive channel a plugin uses to reach the person."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vtx.plugins.plugin import plugin


class UIService:
    """The UI seam.

    ``set_context`` installs the host implementation once, at launch. Without
    one every method is a safe no-op, so a headless run and a plugin written
    against the interactive surface both work unchanged.
    """

    def __init__(self) -> None:
        self._context: Any = None
        self._fallback: Callable[[str, str], None] | None = None

    def set_context(self, context: Any) -> Any:
        self._context = context
        return context

    def set_fallback(self, fn: Callable[[str, str], None]) -> Callable[[str, str], None]:
        self._fallback = fn
        return fn

    @property
    def installed(self) -> bool:
        return self._context is not None

    def notify(self, message: str, level: str = "info") -> None:
        """Show ``message``. Silent, not raising, when no UI is installed."""
        context = self._context
        if context is not None:
            notify = getattr(context, "notify", None)
            if notify is not None:
                notify(message, level)
                return
        if self._fallback is not None:
            self._fallback(message, level)

    def ask(self, question: str, options: Any = None) -> str | None:
        """Ask the person a question. Returns ``None`` with no UI installed."""
        context = self._context
        if context is not None:
            ask = getattr(context, "ask", None)
            if ask is not None:
                return ask(question, options)
        if self._fallback is not None:
            self._fallback(question, "ask")
        return None


@plugin(name="ui", inject=())
def apply(ctx, config):
    ctx.provide("ui", UIService())


PLUGIN = apply
