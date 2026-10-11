"""The event-bus face a caller sees, backed by the composition's :class:`EventHub`.

This is the deprecation boundary, and it is deliberately thin. ``vtx.agent``'
existing call sites emit through an object with ``emit``/``emit_sync``/``on``/
``handler_count``; rather than rewrite all of them in one change, they get an
adapter that carries exactly those four methods onto the one hub the composition
owns.

The semantics are unchanged, and that is the point: a blocking event still
returns a merged decision dict where the first ``block`` wins, and ``args`` and
``output`` still chain through the handlers that follow. A *new* caller should use
``ctx.emit`` and the dispatch modes directly — the adapter exists so old call
sites keep working while they migrate, and it is not the API to grow.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from vtx.plugins.api import NO_OP_UI_CONTEXT
from vtx.plugins.event_contract import (
    ALL_EVENTS,
    ARGS,
    BLOCK,
    EVENT_MODES,
    OUTPUT,
    build_event_object,
    merge_results,
)
from vtx.plugins.events import DispatchMode, EventHub

log = logging.getLogger("vtx.plugins.bus")


@dataclass
class HandlerContext:
    """Per-event context passed to handlers that accept a third argument.

    A handler written ``(event, payload, ctx)`` receives this; ``ctx.ui`` is the
    installed host UI, or a no-op context when none is.
    """

    ui: Any = None
    cwd: str = ""
    mode: str = "print"
    session_manager: Any = None
    model_registry: Any = None

    @property
    def model(self) -> Any:
        return getattr(self.model_registry, "model", None)

    @property
    def thinking_level(self) -> str | None:
        return getattr(self.model_registry, "thinking_level", None)


class ExtensionBus:
    """A bus-shaped view of one :class:`EventHub`.

    ``emit`` and ``emit_sync`` both return the merged handler decision, so a
    caller that used to write ``merged = bus.emit("tool_call", ...)`` and read
    ``merged.get("block")`` behaves identically.
    """

    def __init__(self, hub: EventHub | None = None) -> None:
        self._hub = hub or EventHub()
        for name in ALL_EVENTS:
            self._hub.declare(name, EVENT_MODES.get(name, DispatchMode.SERIAL))
        self._ui_context: Any = None
        self._cwd: str = ""
        self._mode: str = "print"
        # The wrapped form ``on`` handed to the hub, kept per handler so ``off``
        # can remove exactly what was added. ``_wrap`` builds a fresh closure on
        # every call, so rebuilding it here would not match what the hub holds.
        self._subscriptions: dict[str, list[tuple[Callable[..., Any], Callable[..., Any]]]] = {}

    # ---- registration ----------------------------------------------------

    def on(self, event: str, handler: Callable[..., Any] | None = None) -> Any:
        """Subscribe ``handler``. Returns the handler for decorator use.

        ``handler`` is called as ``(event, payload)``, with ``payload`` wrapped in
        its typed event object. A handler that declares a third parameter also
        receives a :class:`HandlerContext`.
        """
        if event not in ALL_EVENTS:
            raise ValueError(f"Unknown event {event!r}. Valid events are: {', '.join(ALL_EVENTS)}")
        if handler is None:

            def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
                self.on(event, fn)
                return fn

            return decorator
        wrapped = self._wrap(event, handler)
        self._hub.on(event, wrapped)
        self._subscriptions.setdefault(event, []).append((handler, wrapped))
        return handler

    def off(self, event: str, handler: Callable[..., Any]) -> None:
        """Unsubscribe a handler previously passed to :meth:`on`.

        A no-op when the handler was never subscribed, so a teardown path can
        call it defensively. Without this the only way to remove a handler was
        to rebuild the hub, which left a caller that registered directly --
        rather than through a plugin row -- with no way to unregister.
        """
        subscriptions = self._subscriptions.get(event, [])
        for index, (registered, wrapped) in enumerate(subscriptions):
            if registered is handler:
                del subscriptions[index]
                if not subscriptions:
                    self._subscriptions.pop(event, None)
                self._hub.off(event, wrapped)
                return

    def _wrap(self, event: str, handler: Callable[..., Any]) -> Callable[..., Any]:
        """Adapt one handler to the hub's single-argument calling convention.

        The hub calls a listener with one value; the bus's contract is
        ``(event, payload)`` with an optional third ``ctx`` parameter, so the
        event name and the context are supplied here rather than at the call
        site.
        """

        def wrapped(payload: Any) -> Any:
            if _accepts_third(handler):
                return handler(event, payload, self._context())
            return handler(event, payload)

        return wrapped

    def handler_count(self, event: str) -> int:
        return len(self._hub.handlers(event))

    def has_handlers(self, event: str) -> bool:
        return self.handler_count(event) > 0

    # ---- dispatch --------------------------------------------------------

    async def emit(
        self, event: str, *, cancel_event: Any = None, **payload: Any
    ) -> dict[str, Any]:
        """Fire ``event`` and return the merged handler decision.

        Handlers run in registration order, and the **first one that blocks stops
        the rest from running at all** — a policy handler that denies a tool call
        prevents any later handler from seeing it.
        """
        wrapped = _AdaptedPayload(event, payload)
        results: list[Any] = []
        for handler in list(self._hub.handlers(event)):
            try:
                result = handler(wrapped)
                if asyncio.iscoroutine(result):
                    result = await result
            except Exception:
                log.exception("handler for %s raised", event)
                continue
            results.append(result)
            if isinstance(result, dict):
                if result.get(BLOCK):
                    break
                # Write a rewrite back so the next handler chains off it.
                for key in (ARGS, OUTPUT):
                    if key in result:
                        wrapped[key] = result[key]
        return merge_results(results)

    def emit_sync(self, event: str, **payload: Any) -> dict[str, Any]:
        """Fire ``event`` without awaiting handlers.

        Used before the asyncio loop is running (``session_start`` on TUI mount,
        ``session_end`` on shutdown). A handler that returns a coroutine is not
        awaited — it is logged and dropped rather than silently lost.
        """
        wrapped = _AdaptedPayload(event, payload)
        results: list[Any] = []
        for handler in list(self._hub.handlers(event)):
            try:
                result = handler(wrapped)
            except Exception:
                log.exception("handler for %s raised", event)
                continue
            if asyncio.iscoroutine(result):
                log.warning(
                    "handler %r for %s returned a coroutine; use a sync handler or "
                    "defer to the async emit",
                    getattr(handler, "__qualname__", handler),
                    event,
                )
                result.close()
                continue
            results.append(result)
            if isinstance(result, dict):
                if result.get(BLOCK):
                    break
                for key in (ARGS, OUTPUT):
                    if key in result:
                        wrapped[key] = result[key]
        return merge_results(results)

    # ---- UI --------------------------------------------------------------

    def set_ui_context(self, ui_context: Any, *, cwd: str = "", mode: str = "print") -> None:
        """Install the host UI so three-argument handlers get interactive ``ctx.ui``."""
        self._ui_context = ui_context
        self._cwd = cwd
        self._mode = mode

    # ---- internals -------------------------------------------------------

    def _context(self) -> HandlerContext:
        """The context handed to a three-argument handler."""
        return HandlerContext(
            ui=self._ui_context if self._ui_context is not None else NO_OP_UI_CONTEXT,
            cwd=self._cwd,
            mode=self._mode,
        )


class _AdaptedPayload(dict):
    """A payload mapping that also exposes its fields as attributes.

    The old bus handed a handler either the raw dict or a typed event object
    depending on its arity. Passing this keeps both working: it subclasses
    ``dict`` for the callers that index, and ``build_event_object`` fills the
    attributes for the ones that read ``payload.tool_name``.
    """

    def __init__(self, event: str, payload: dict[str, Any], bus: Any = None) -> None:
        super().__init__(payload)
        object.__setattr__(self, "_event", event)
        object.__setattr__(self, "_bus", bus)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            event = object.__getattribute__(self, "_event")
            built = build_event_object(event, dict(self))
            if hasattr(built, name):
                return getattr(built, name)
            raise AttributeError(name) from None


def _accepts_third(handler: Callable[..., Any]) -> bool:
    """Whether ``handler`` declares a third positional parameter.

    Never memoised. The dict that used to hold this keyed on ``id(handler)``,
    and CPython reuses a freed function's address for the next function it
    allocates, so a dead three-argument handler would hand its ``True`` to an
    unrelated two-argument one. Dispatch swallowed the resulting ``TypeError``,
    which turned that into a handler that silently never ran — exactly the
    failure ``/reload`` is supposed to prevent.
    """
    import inspect

    try:
        parameters = inspect.signature(handler).parameters.values()
    except (TypeError, ValueError):
        return False
    positional = sum(
        1 for p in parameters if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    )
    return positional >= 3 or any(p.kind is p.VAR_POSITIONAL for p in parameters)


#: The name every existing call site uses. :class:`ExtensionBus` is the same
#: object; the alias is the migration seam, not a second implementation.
EventBus = ExtensionBus


__all__ = ["EventBus", "ExtensionBus"]
