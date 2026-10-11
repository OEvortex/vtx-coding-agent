"""``ctx.commands`` — human commands that dispatch without a model turn."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vtx.plugins.plugin import plugin

CommandFn = Callable[..., Any]


class CommandService:
    """The slash-command registry.

    A command is a name plus a callable. Registering one does not cost a model
    turn, which is what separates a command from a tool: the human typed it, so
    there is nothing to infer.
    """

    def __init__(self) -> None:
        self._commands: dict[str, tuple[CommandFn, str]] = {}

    def register(self, name: str, fn: CommandFn, description: str = "") -> CommandFn:
        if not name:
            raise ValueError("command name must be non-empty")
        self._commands[name] = (fn, description)
        return fn

    def unregister(self, name: str) -> bool:
        return self._commands.pop(name, None) is not None

    def get(self, name: str) -> CommandFn | None:
        entry = self._commands.get(name)
        return entry[0] if entry else None

    def names(self) -> list[str]:
        return sorted(self._commands)

    def describe(self, name: str) -> str:
        entry = self._commands.get(name)
        return entry[1] if entry else ""

    def dispatch(self, name: str, *args: Any, **kwargs: Any) -> Any:
        fn = self.get(name)
        if fn is None:
            raise KeyError(f"no command named {name!r}")
        return fn(*args, **kwargs)

    def __contains__(self, name: str) -> bool:
        return name in self._commands


@plugin(name="commands", inject=())
def apply(ctx, config):
    ctx.provide("commands", CommandService())


PLUGIN = apply
