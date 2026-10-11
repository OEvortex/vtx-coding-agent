"""Typed event dispatch with an explicit mode per event name.

The dispatch mode is part of an event's public contract, so it is declared once
and checked: registering a handler under a name already declared with a
different mode fails at load rather than silently changing what every other
listener sees.

Waterfall semantics follow the around-middleware shape: a listener receives the
piped value as its first positional argument plus a ``next`` callable. Calling
``await next(value)`` delegates ``value`` to the rest of the chain and resolves
to its result; returning without calling it short-circuits. A listener that only
observes must delegate.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections import defaultdict
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from vtx.plugins.errors import EventModeError

Handler = Callable[..., Any]


class DispatchMode(StrEnum):
    EMIT = "emit"
    WATERFALL = "waterfall"
    PARALLEL = "parallel"
    SERIAL = "serial"
    BAIL = "bail"


class _Disposer:
    """A callable teardown that also carries the registration it removes."""

    __slots__ = ("fn", "label")

    def __init__(self, fn: Callable[[], Any], label: str = "") -> None:
        self.fn = fn
        self.label = label

    def __call__(self) -> Any:
        return self.fn()

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<disposer {self.label}>"


_CO_VARKEYWORDS = 0x08


def _accepts_var_keyword(handler: Handler) -> bool:
    """Whether ``handler`` declares ``**kwargs``.

    Read straight off the code object, which is exact and costs nothing. This
    used to be memoised in a dict keyed by ``id(handler)``; that was wrong
    rather than merely slow, because CPython hands a freed function's address
    straight back to the next function it allocates, so an unrelated handler
    could inherit a dead one's answer.
    """
    code = getattr(handler, "__code__", None)
    if code is not None:
        return bool(code.co_flags & _CO_VARKEYWORDS)
    try:
        parameters = inspect.signature(handler).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(p.kind is p.VAR_KEYWORD for p in parameters)


class EventHub:
    """One registry of handlers, one dispatch mode per event name."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._modes: dict[str, DispatchMode] = {}

    # ---- declaration ----------------------------------------------------

    def declare(self, name: str, mode: DispatchMode) -> None:
        """Fix ``name``'s dispatch mode."""
        existing = self._modes.get(name)
        if existing is not None and existing != mode:
            raise EventModeError(
                f"event {name!r} is declared {existing.value} and cannot be "
                f"re-declared {mode.value}"
            )
        self._modes[name] = mode

    def mode(self, name: str) -> DispatchMode:
        """The declared mode for ``name``; events nobody declared are ``emit``."""
        return self._modes.get(name, DispatchMode.EMIT)

    def names(self) -> list[str]:
        return sorted(self._modes)

    # ---- subscription ---------------------------------------------------

    def on(
        self,
        name: str,
        handler: Handler | None = None,
        *,
        mode: DispatchMode | None = None,
        prepend: bool = False,
    ) -> Any:
        """Subscribe ``handler`` to ``name``.

        Two call forms are supported, matching the form used elsewhere in vtx::

            hub.on("session_start", handler)
            @hub.on("session_start")
            def handler(...): ...

        Returns the handler so the decorator form is transparent, and registers
        a teardown on the owning context via :meth:`attach`.
        """
        if mode is not None:
            self.declare(name, mode)
        if handler is None:

            def decorator(fn: Handler) -> Handler:
                self.on(name, fn, prepend=prepend)
                return fn

            return decorator
        handlers = self._handlers[name]
        if prepend:
            handlers.insert(0, handler)
        else:
            handlers.append(handler)
        return handler

    def off(self, name: str, handler: Handler) -> None:
        """Remove one handler. Silent if it was never registered."""
        handlers = self._handlers.get(name)
        if not handlers:
            return
        with contextlib.suppress(ValueError):
            handlers.remove(handler)
        if not handlers:
            self._handlers.pop(name, None)

    def handlers(self, name: str) -> tuple[Handler, ...]:
        return tuple(self._handlers.get(name, ()))

    # ---- dispatch -------------------------------------------------------

    async def emit(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Dispatch ``name`` with its declared mode.

        Every mode is awaited; ``emit`` simply discards return values, so a
        handler that must not block the caller uses :meth:`emit_nowait`.
        """
        mode = self.mode(name)
        handlers = list(self._handlers.get(name, ()))
        if mode is DispatchMode.EMIT:
            for handler in handlers:
                await self._invoke(handler, args, kwargs)
            return None
        if mode is DispatchMode.SERIAL:
            return [await self._invoke(handler, args, kwargs) for handler in handlers]
        if mode is DispatchMode.PARALLEL:
            if not handlers:
                return []
            return list(
                await asyncio.gather(
                    *(self._invoke(handler, args, kwargs) for handler in handlers)
                )
            )
        if mode is DispatchMode.BAIL:
            for handler in handlers:
                result = await self._invoke(handler, args, kwargs)
                if result is not None:
                    return result
            return None
        return await self._waterfall(handlers, args, kwargs)

    def emit_nowait(self, name: str, *args: Any, **kwargs: Any) -> asyncio.Task[Any] | None:
        """Schedule ``emit`` on the running loop; returns ``None`` without one."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        return loop.create_task(self.emit(name, *args, **kwargs))

    # ---- internals ------------------------------------------------------

    @staticmethod
    async def _invoke(
        handler: Handler, args: tuple[Any, ...], kwargs: dict[str, Any], next_: Any = None
    ) -> Any:
        if next_ is not None:
            # A listener written ``(value, next)`` still works, and one written
            # ``(value, next, **kwargs)`` sees the emitter's extra keyword
            # arguments; one that declares neither is called with just the two.
            if not _accepts_var_keyword(handler):
                kwargs = {}
            result = handler(*args, next_, **kwargs)
        else:
            result = handler(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _waterfall(
        self, handlers: list[Handler], args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        if not handlers:
            return args[0] if args else None

        async def chain(index: int, current: tuple[Any, ...], extra: dict[str, Any]) -> Any:
            if index >= len(handlers):
                return current[0] if current else None
            handler = handlers[index]

            async def next_(*new_args: Any, **new_kwargs: Any) -> Any:
                return await chain(
                    index + 1, new_args if new_args else current, {**extra, **new_kwargs}
                )

            return await self._invoke(handler, current, extra, next_=next_)

        return await chain(0, args, kwargs)


__all__ = ["DispatchMode", "EventHub", "Handler", "_Disposer"]
