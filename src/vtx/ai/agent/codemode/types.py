"""Public types for the codemode sandbox.

A sandbox is constructed once with its tools and limits, then executed many
times. Each :meth:`CodemodeSandbox.execute` call is one-shot -- a fresh
process, a fresh namespace -- so nothing leaks between runs except what the
script explicitly wrote to the store.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

#: JSON that can cross the sandbox boundary. Mirrors opencode's DataValue.
JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

#: Per-value cap for a store entry, in characters of JSON.
MAX_STORE_VALUE_CHARS = 256 * 1024

#: Aggregate cap for all store values together.
MAX_STORE_TOTAL_CHARS = 1024 * 1024


@dataclass(frozen=True)
class CodemodeTool:
    """One tool the sandbox may call.

    ``input_schema`` and ``output_schema`` are JSON Schema. They shape the
    declaration the model reads; values are *not* validated against them. That
    is deliberate and it is the one place this implementation is looser than
    the reference: validation happens host-side, in the tool's own
    implementation, because the host is the only side that knows what a valid
    argument actually means.
    """

    name: str
    description: str
    #: ``async (args, signal) -> Any``. The value must be JSON-serializable.
    execute: Callable[[dict[str, Any], Any], Awaitable[Any]] = field(repr=False)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None

    def identifier(self) -> str:
        """Return the Python identifier the script calls this tool by.

        Non-identifier characters become ``_``, so ``list-issues`` is reachable
        as both ``tools.list_issues(...)`` and ``getattr(tools, "list-issues")``.
        The mapping is applied in :data:`DECLARATION_RESERVED` order so a name
        that would collide with a builtin is not silently shadowed.
        """
        from vtx.ai.agent.codemode.declarations import to_identifier

        return to_identifier(self.name)


@runtime_checkable
class ToolRunner(Protocol):
    """What the sandbox needs from the host to run one tool call."""

    async def run_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Execute one admitted call and return its JSON-safe value."""
        ...


@dataclass(frozen=True)
class ToolCall:
    """One tool call the sandbox admitted, in call order."""

    name: str
    status: str
    kind: str | None = None
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class Diagnostic:
    """A failure, as data.

    Carries one of :data:`vtx.ai.agent.codemode.errors.KINDS` plus a
    model-facing message. Never a traceback the model must parse.
    """

    kind: str
    message: str
    stack: str | None = None


@dataclass(frozen=True)
class Result:
    """The outcome of one execution.

    Exactly one of ``value``/``diagnostic`` is meaningful, keyed by ``ok``.
    ``output`` holds ordered ``text()`` and ``console`` items produced before
    the end, and is populated for failures too: output produced before the
    failure is still worth having.
    """

    ok: bool
    value: Any = None
    output: tuple[Mapping[str, Any], ...] = ()
    calls: tuple[ToolCall, ...] = ()
    diagnostic: Diagnostic | None = None
    #: Keys the script set, and keys it deleted. Applied by the host, and only
    #: when ``ok`` -- a failed run reports no writes.
    store_writes: Mapping[str, Any] = field(default_factory=dict)
    store_deletes: frozenset[str] = frozenset()

    def apply_to_store(self, store: dict[str, Any]) -> None:
        """Commit staged writes onto ``store`` in place."""
        for key in self.store_deletes:
            store.pop(key, None)
        store.update(self.store_writes)


@dataclass(frozen=True)
class Limits:
    """Resource budgets for one execution.

    One knob. ``timeout_ms`` is a wall-clock deadline enforced by killing the
    process, so a busy loop and a hung tool call die the same way.

    A call-count budget and an output-size cap would both be reasonable, but
    neither is implemented, so neither is accepted: offering a limit that does
    nothing is worse than not having one. Both are easy to add at the host — a
    counter in ``_serve`` and a size check in ``_result_from_frame``.
    """

    timeout_ms: int | None = 30_000


def coerce_json(value: Any, *, what: str) -> JsonValue:
    """Return ``value`` as JSON-safe data, or raise :class:`InvalidOutput`.

    Imported lazily to keep the module free of runtime dependencies; the real
    implementation lives in :mod:`vtx.ai.agent.codemode.jsonio` so the worker
    and the host agree on exactly what "JSON-safe" means.
    """
    from vtx.ai.agent.codemode.jsonio import coerce_json as _coerce

    return _coerce(value, what=what)


__all__ = [
    "MAX_STORE_TOTAL_CHARS",
    "MAX_STORE_VALUE_CHARS",
    "CodemodeTool",
    "Diagnostic",
    "JsonValue",
    "Limits",
    "Result",
    "ToolCall",
    "ToolRunner",
    "coerce_json",
]
