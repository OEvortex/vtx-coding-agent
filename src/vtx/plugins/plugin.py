"""What a plugin is, and how unrelated objects are recognised as one.

A plugin is anything with a name, an optional list of required services, and an
``apply`` that runs against a :class:`~vtx.plugins.context.Context`. Written out
as a function it is four lines::

    @plugin(name="tool-bash", inject=("tools",))
    def apply(ctx, config):
        ctx.tools.register(BashTool())

Written as a class it is the same shape with a lifecycle::

    class tool_bash(Service):
        name = "tool-bash"
        inject = ("tools",)
        def start(self, ctx, config): ...
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

ApplyFn = Any


@dataclass(frozen=True)
class Plugin:
    """One mountable unit: what it is called, what it needs, what it does."""

    name: str
    apply: ApplyFn
    inject: tuple[str, ...] = ()
    config: type[Any] | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        inject = "".join(f",{k}" for k in self.inject)
        return f"<Plugin {self.name} inject=[{inject[1:]}]>"


def plugin(
    name: str,
    *,
    inject: tuple[str, ...] | list[str] = (),
    config: type[Any] | None = None,
    **meta: Any,
) -> Any:
    """Decorator turning a function into a :class:`Plugin`."""

    def decorator(fn: ApplyFn) -> Plugin:
        return Plugin(name=name, apply=fn, inject=tuple(inject), config=config, meta=dict(meta))

    return decorator


class Service:
    """Base class for a plugin written as an object with a ``start`` method."""

    name: ClassVar[str] = ""
    inject: ClassVar[tuple[str, ...]] = ()
    #: Optional pydantic model describing this plugin's row config.
    Config: ClassVar[Any] = None

    def start(self, ctx: Any, config: Any) -> None:
        """Mount this plugin. Override; register teardown on ``ctx.effect``."""
        raise NotImplementedError


def as_plugin(obj: Any) -> Plugin:
    """Normalise ``obj`` into a :class:`Plugin`.

    Accepted shapes, in order: a :class:`Plugin`; a :class:`Service` instance or
    subclass; any object exposing ``name``/``inject``/``apply``; any callable.
    """
    if isinstance(obj, Plugin):
        return obj
    if isinstance(obj, Service):
        return Plugin(
            name=obj.name or type(obj).__name__,
            apply=obj.start,
            inject=tuple(obj.inject),
            config=obj.Config,
        )
    if isinstance(obj, type) and issubclass(obj, Service):
        return as_plugin(obj())
    apply = getattr(obj, "apply", None)
    if apply is not None:
        name = getattr(obj, "name", None) or getattr(obj, "__name__", None) or "plugin"
        inject = getattr(obj, "inject", ()) or ()
        config = getattr(obj, "Config", None) or getattr(obj, "config", None)
        return Plugin(name=str(name), apply=apply, inject=tuple(inject), config=config)
    if callable(obj):
        name = getattr(obj, "__name__", "plugin")
        inject = getattr(obj, "inject", ()) or ()
        config = getattr(obj, "Config", None) or getattr(obj, "config", None)
        return Plugin(name=str(name), apply=obj, inject=tuple(inject), config=config)
    raise TypeError(f"{obj!r} is not a plugin: it has no apply and is not callable")


def apply_plugin(spec: Plugin, ctx: Any, config: Any) -> Any:
    """Run ``spec`` against ``ctx`` with an already-validated ``config``."""
    return spec.apply(ctx, config)


__all__ = ["ApplyFn", "Plugin", "Service", "apply_plugin", "as_plugin", "plugin"]
