"""Failure taxonomy for the kernel's host bridge.

opencode's CodeMode design makes the same split, and states the reason
plainly: agents should be able to *recover accurately* instead of treating
every failure as a generic execution error. A cell that says ``Tool not
found: goal`` and a cell that says ``goal refused: not your session`` and a
cell whose traceback ends inside a host defect need three different responses —
try a different name, change the arguments or approach, or stop and report.
Collapsing them into one traceback loses the only information the model needs
to choose.

Two rules carry over from the reference:

- **Public/private split.** A bridge error carries a ``message`` safe to show
  the model and a ``detail`` that stays in host logs. Tool authors get to
  decide how much of a failure is model-visible.
- **Sanitize by default.** An exception nobody classified is an
  ``execution_failure`` and its text is not forwarded verbatim; a raw Python
  traceback across the bridge can leak host paths and internals into context.
"""

from __future__ import annotations

from typing import Any, Final

#: The tool name is not registered, or is not visible to this session.
UNKNOWN_TOOL: Final = "unknown_tool"
#: The tool exists but rejected the arguments it was given.
INVALID_INPUT: Final = "invalid_input"
#: The tool ran and reported failure. The message is the tool's own.
TOOL_FAILURE: Final = "tool_failure"
#: The tool succeeded but its result cannot cross the bridge as plain data.
INVALID_OUTPUT: Final = "invalid_output"
#: The bridge itself is not wired up in this session.
HOST_UNAVAILABLE: Final = "host_unavailable"
#: The call exceeded its time budget.
TIMEOUT: Final = "timeout"
#: An unclassified host defect. Sanitized.
EXECUTION_FAILURE: Final = "execution_failure"

KINDS: Final = (
    UNKNOWN_TOOL,
    INVALID_INPUT,
    TOOL_FAILURE,
    INVALID_OUTPUT,
    HOST_UNAVAILABLE,
    TIMEOUT,
    EXECUTION_FAILURE,
)

#: What the model should try next, per kind. This is the point of the
#: taxonomy: the category exists to change behavior, not just to label.
_REMEDY: Final = {
    UNKNOWN_TOOL: (
        'Search for the tool instead of guessing its name: `find_tools("<what you '
        'need>")` returns candidates, and `describe_tool(name)` gives the exact '
        "parameters to call it with."
    ),
    INVALID_INPUT: (
        "The call was well-named but the arguments were wrong. Re-read the tool's "
        "parameters and call it again with a corrected payload — do not retry the "
        "identical call."
    ),
    TOOL_FAILURE: (
        "The tool ran and declined. Read what it said and change your approach; "
        "repeating the same call will fail the same way."
    ),
    INVALID_OUTPUT: (
        "The tool produced a result that cannot be sent back as data. Narrow the "
        "call (fewer records, a summary, a filter) so the return value is plain JSON."
    ),
    HOST_UNAVAILABLE: (
        "Do the work inside the cell instead, or delegate it to a sub-agent. "
        "Retrying the call cannot help."
    ),
    TIMEOUT: (
        "The call ran too long. Retry with less work per call — a narrower range, "
        "a smaller limit, or a filter before the call rather than after."
    ),
    EXECUTION_FAILURE: (
        "The host failed in a way that is not a tool problem. Do not retry the same "
        "call. Use a different approach, or report this and move on."
    ),
}

#: Whether retrying the identical call could plausibly succeed.
_RETRYABLE: Final = frozenset({TIMEOUT})


class BridgeError(Exception):
    """A host-bridge failure carrying a category the model can act on.

    Args:
        kind: One of :data:`KINDS`.
        message: Model-safe description. Never include a raw traceback.
        detail: Host-side cause, kept out of the model-facing text.
        suggestions: Concrete next steps, prepended to the standard remedy.
    """

    def __init__(
        self, kind: str, message: str, *, detail: str = "", suggestions: tuple[str, ...] = ()
    ) -> None:
        super().__init__(message)
        if kind not in KINDS:
            raise ValueError(f"unknown bridge failure kind: {kind!r}")
        self.kind = kind
        self.message = message
        self.detail = detail
        self.suggestions = suggestions

    @property
    def retryable(self) -> bool:
        """Whether an identical retry could plausibly succeed."""
        return self.kind in _RETRYABLE

    def render(self) -> str:
        """The model-facing rendering: category, message, and what to do."""
        lines = [f"[bridge:{self.kind}] {self.message}"]
        for suggestion in self.suggestions:
            lines.append(f"  - {suggestion}")
        lines.append(f"  Next: {_REMEDY[self.kind]}")
        return "\n".join(lines)

    def __str__(self) -> str:
        # The kernel surfaces an exception to the model as its ``str()``, so the
        # category and the remedy have to ride along here or they are lost.
        return self.render()

    def __repr__(self) -> str:
        return f"BridgeError(kind={self.kind!r}, message={self.message!r}, detail={self.detail!r})"


def _is_unknown_tool(exc: BaseException) -> bool:
    """Whether an exception means "no such tool" without string-sniffing types.

    Both the harness and the bridge raise a plain ``ValueError`` for this, so
    the marker is checked rather than the class.
    """
    text = str(exc)
    return isinstance(exc, ValueError) and (
        "not found" in text.lower() or "no such" in text.lower()
    )


def classify_bridge_error(
    name: str, exc: BaseException, *, available: tuple[str, ...] = ()
) -> BridgeError:
    """Map an arbitrary executor exception onto the bridge taxonomy.

    Anything not recognized becomes an :data:`EXECUTION_FAILURE` with a
    sanitized message, so a new or exotic host exception leaks its type name at
    most.
    """
    if isinstance(exc, BridgeError):
        return exc

    if isinstance(exc, TimeoutError):
        return BridgeError(TIMEOUT, f"'{name}' exceeded its time budget.", detail=repr(exc))

    # pydantic's ValidationError subclasses ValueError, and its messages can
    # contain "not found" ("field not found in ..."), so it is matched by type
    # name before the tool-name sniff rather than after it.
    if type(exc).__name__ == "ValidationError":
        return BridgeError(
            INVALID_INPUT, f"'{name}' rejected the arguments it was given.", detail=repr(exc)
        )

    if _is_unknown_tool(exc):
        suggestions = (
            (
                (
                    f"Available tools: {', '.join(sorted(available))}. Or search for the "
                    'capability: find_tools("what you need") then describe_tool(name).'
                ),
            )
            if available
            else ()
        )
        return BridgeError(
            UNKNOWN_TOOL,
            f"No tool named '{name}' is available in this session.",
            detail=repr(exc),
            suggestions=suggestions,
        )

    if isinstance(exc, RuntimeError):
        # The executor wraps a tool's own failure in RuntimeError, so this text
        # is authored by the tool and is safe to forward.
        return BridgeError(TOOL_FAILURE, str(exc) or f"'{name}' failed.", detail=repr(exc))

    return BridgeError(
        EXECUTION_FAILURE,
        f"'{name}' failed inside the host ({type(exc).__name__}).",
        detail=repr(exc),
    )


def plain_data(value: Any) -> Any:
    """Coerce a tool result to plain JSON data, or explain why it cannot cross.

    ``default=str`` keeps working tools working, so a result that is not
    JSON-native still crosses as its ``str()``. What must never happen is the
    *failure* path leaking: the serializer's own message and any exception it
    raised can carry the offending value's repr, host paths, and internals
    straight into model context.

    Raises:
        BridgeError: :data:`INVALID_OUTPUT` when the value cannot cross. The
            offending type is named; the value is not echoed.
    """
    import json

    try:
        return json.loads(json.dumps(value, default=str))
    except Exception as exc:
        # Deliberately broad: `default=str` means a value only fails here if
        # the structure is circular or `__str__` itself raises, and the latter
        # would otherwise propagate a host exception across the bridge raw.
        raise BridgeError(
            INVALID_OUTPUT,
            f"Result of type {type(value).__name__} could not be converted to "
            "plain data, so it cannot cross the bridge. Return a str, dict, or "
            "list built from plain values.",
            detail=f"{type(exc).__name__}: {exc}",
        ) from None
