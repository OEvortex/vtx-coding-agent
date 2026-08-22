from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable


@runtime_checkable
class BaseTool(Protocol):
    """Minimal interface required by the protocol layer for permission checks."""

    name: str
    mutating: bool = True


@runtime_checkable
class BaseProvider(Protocol):
    """Minimal interface required by the protocol layer for handoff/compaction."""

    name: str

    async def stream(
        self,
        messages: list[object],
        *,
        system_prompt: str | None = None,
        tools: list[object] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[object]: ...
