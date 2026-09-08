from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass(slots=True)
class CommandContext:
    channel: str | None = None
    sender: str | None = None
    args: list[str] = field(default_factory=list)
    raw: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class Command(ABC):
    name: str = ""
    description: str = ""
    aliases: ClassVar[list[str]] = []

    @abstractmethod
    async def execute(self, ctx: CommandContext) -> str: ...


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, cmd: Command) -> None:
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._commands[alias] = cmd

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    def all(self) -> list[Command]:
        seen: set[int] = set()
        out: list[Command] = []
        for c in self._commands.values():
            if id(c) not in seen:
                seen.add(id(c))
                out.append(c)
        return out

    async def dispatch(self, name: str, ctx: CommandContext) -> str:
        cmd = self.get(name)
        if cmd is None:
            return f"unknown command: {name}"
        return await cmd.execute(ctx)
