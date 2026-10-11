"""The context: a repository of services plus reversible effects.

A plugin never imports another plugin. It names the services it needs, and the
loader mounts it only once those services exist. Every contribution it makes
goes through :meth:`Context.effect` or :meth:`Context.on`, so unloading a plugin
unwinds exactly what it registered, in reverse order.

A child context (see :meth:`scope`) is a scoped view for one agent or session:
it reads its parent's services, shadows the ones it re-provides, and disposes
independently.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Generator
from typing import Any

from vtx.plugins.errors import ServiceMissingError
from vtx.plugins.events import DispatchMode, EventHub

#: The service keys the kernel understands. Anything else works too, but these
#: are the ones with a typed property on :class:`Context`.
STANDARD_KEYS: frozenset[str] = frozenset(
    {
        "agents",
        "agentLoop",
        "commands",
        "compaction",
        "goals",
        "llm",
        "mcp",
        "permissions",
        "prompts",
        "sandbox",
        "sessionProjections",
        "sessions",
        "skills",
        "telemetry",
        "tools",
    }
)


class Context:
    """One plugin's view of the running composition."""

    def __init__(
        self,
        *,
        hub: EventHub | None = None,
        parent: Context | None = None,
        label: str = "",
        service_owner: Context | None = None,
    ) -> None:
        self._services: dict[str, Any] = {}
        self._effects: list[Callable[[], Any]] = []
        self._provided: list[str] = []
        self._children: list[Context] = []
        self._parent = parent
        self._hub = hub if hub is not None else (parent.hub if parent is not None else EventHub())
        self.label = label
        #: When set, ``provide`` writes here instead of locally. The host uses
        #: it so every plugin row publishes into one composition-wide service
        #: table while keeping its own effect list.
        self._service_owner = service_owner

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        keys = ", ".join(sorted(self._services))
        return f"<Context {self.label or 'root'} services=[{keys}]>"

    # ---- services -------------------------------------------------------

    def provide(self, key: str, service: Any) -> Any:
        """Publish ``service`` under ``key``.

        A plugin row publishes into the composition's shared service table, so
        every other row sees it regardless of mount order. A scope created by
        :meth:`scope` publishes locally, shadowing the parent for its own
        subtree only. Effects registered afterwards can tear it down; there is
        no un-provide, because disposal is the point.
        """
        owner = self._service_owner if self._service_owner is not None else self
        owner._services[key] = service
        if owner is not self:
            self._provided.append(key)
        return service

    set = provide

    def require(self, key: str) -> Any:
        """Return the service under ``key``, or raise :class:`ServiceMissingError`."""
        try:
            return self[key]
        except KeyError as exc:
            raise ServiceMissingError(
                f"no plugin provides the service {key!r}"
                + (f" to {self.label!r}" if self.label else "")
            ) from exc

    def get(self, key: str, default: Any = None) -> Any:
        ctx: Context | None = self
        while ctx is not None:
            if key in ctx._services:
                return ctx._services[key]
            ctx = ctx._parent
        return default

    def __contains__(self, key: str) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def __getitem__(self, key: str) -> Any:
        ctx: Context | None = self
        while ctx is not None:
            services = ctx._services
            if key in services:
                return services[key]
            ctx = ctx._parent
        raise KeyError(key)

    def __getattr__(self, name: str) -> Any:
        # ``ctx.tools`` and friends. Dunder and private names never reach here
        # through normal use, but guard anyway so copy/pickle cannot recurse.
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    # ---- effects --------------------------------------------------------

    def effect(self, fn: Callable[[], Any]) -> Callable[[], Any]:
        """Register ``fn`` as a teardown, unwound in reverse on :meth:`dispose`."""
        self._effects.append(fn)
        return fn

    @contextlib.contextmanager
    def scoped(self) -> Generator[Context]:
        """A child context for one agent's lifetime."""
        child = self.scope()
        try:
            yield child
        finally:
            child.dispose()

    def scope(self, label: str = "") -> Context:
        child = Context(parent=self, label=label)
        self._children.append(child)
        return child

    def dispose(self) -> None:
        """Unwind this context's effects and children, newest first."""
        for fn in reversed(self._effects):
            with contextlib.suppress(Exception):
                fn()
        self._effects.clear()
        for child in reversed(self._children):
            child.dispose()
        self._children.clear()
        owner = self._service_owner
        if owner is not None:
            for key in self._provided:
                owner._services.pop(key, None)
        self._provided.clear()
        self._services.clear()
        parent = self._parent
        if parent is not None:
            with contextlib.suppress(ValueError):
                parent._children.remove(self)

    # ---- events ---------------------------------------------------------

    @property
    def hub(self) -> EventHub:
        return self._hub

    def on(
        self,
        name: str,
        handler: Callable[..., Any] | None = None,
        *,
        mode: DispatchMode | None = None,
        prepend: bool = False,
    ) -> Any:
        """Subscribe ``handler`` on this context's hub, and dispose on unload."""
        if handler is None:

            def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
                self._hub.on(name, fn, mode=mode, prepend=prepend)
                self.effect(lambda: self._hub.off(name, fn))
                return fn

            return decorator
        self._hub.on(name, handler, mode=mode, prepend=prepend)
        self.effect(lambda: self._hub.off(name, handler))
        return handler

    async def emit(self, name: str, *args: Any, **kwargs: Any) -> Any:
        return await self._hub.emit(name, *args, **kwargs)

    # ---- typed service accessors ----------------------------------------

    @property
    def tools(self) -> Any:
        return self.require("tools")

    @property
    def llm(self) -> Any:
        return self.require("llm")

    @property
    def sessions(self) -> Any:
        return self.require("sessions")

    @property
    def agents(self) -> Any:
        return self.require("agents")

    @property
    def agent_loop(self) -> Any:
        """Alias of :attr:`agentLoop`, for Python-style callers."""
        return self.require("agentLoop")

    @property
    def agentLoop(self) -> Any:  # noqa: N802 - matches the ctx key
        return self.require("agentLoop")

    @property
    def prompts(self) -> Any:
        return self.require("prompts")

    @property
    def permissions(self) -> Any:
        return self.require("permissions")

    @property
    def compaction(self) -> Any:
        return self.require("compaction")

    @property
    def skills(self) -> Any:
        return self.require("skills")

    @property
    def mcp(self) -> Any:
        return self.require("mcp")

    @property
    def sandbox(self) -> Any:
        return self.require("sandbox")

    @property
    def telemetry(self) -> Any:
        return self.require("telemetry")

    @property
    def goals(self) -> Any:
        return self.require("goals")


class _Missing:
    __slots__ = ()


_MISSING = _Missing()


__all__ = ["STANDARD_KEYS", "Context"]
