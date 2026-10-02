"""Failure taxonomy for the codemode sandbox.

Two layers, because two different things can go wrong and the model recovers
differently for each.

**Sandbox-level** kinds describe the execution itself: ``script`` (raised or
failed to compile), ``timeout``, ``aborted``, ``sandbox`` (worker or transport
failed).

**Tool-level** kinds describe one admitted tool call: ``unknown_tool``,
``invalid_input``, ``tool_failure``, ``invalid_output``, ``host_unavailable``.
A cell that says ``Tool not found: goal`` and one that says ``goal
refused: not your session`` need three different responses — try a different
name, change the arguments, or stop and report. Collapsing them into one
traceback loses the only information the model needs to choose.

Python gets something the JavaScript versions cannot: a real exception class
per kind, so a script can ``except UnknownTool:`` and branch on it.

Two rules carry across all of it:

- **Public/private split.** :class:`ToolError` carries a ``message`` safe to
  show the model and a ``detail`` that stays in host logs.
- **Sanitize by default.** Anything unclassified is sanitized rather than
  forwarded verbatim; a raw traceback crossing the boundary can leak host
  paths and internals into context.
"""

from __future__ import annotations

from typing import Any, Final

#: The script raised, or could not be compiled.
SCRIPT: Final = "script"
#: The deadline expired; the worker process was killed.
TIMEOUT: Final = "timeout"
#: The host signal fired or the sandbox was closed.
ABORTED: Final = "aborted"
#: The worker process or its transport failed (spawn error, protocol error).
SANDBOX: Final = "sandbox"
#: The script is blocked on something that can never complete. Kept apart from
#: TIMEOUT because the cause is provable in a millisecond instead of waited for,
#: and because the fix is a different edit.
STALLED: Final = "stalled"

#: A tool the sandbox was not given was referenced.
UNKNOWN_TOOL: Final = "unknown_tool"
#: The tool exists but rejected the arguments it was given.
INVALID_INPUT: Final = "invalid_input"
#: The tool ran and declined. The message is the tool's own.
TOOL_FAILURE: Final = "tool_failure"
#: The tool succeeded but its result cannot cross the boundary as plain JSON.
INVALID_OUTPUT: Final = "invalid_output"
#: The host bridge is not wired up in this session.
HOST_UNAVAILABLE: Final = "host_unavailable"

#: Every kind the sandbox can report.
KINDS: Final = (
    SCRIPT,
    TIMEOUT,
    ABORTED,
    SANDBOX,
    STALLED,
    UNKNOWN_TOOL,
    INVALID_INPUT,
    TOOL_FAILURE,
    INVALID_OUTPUT,
    HOST_UNAVAILABLE,
)

#: Kinds that describe the execution rather than one tool call. These are
#: terminal: they cannot be caught inside the script.
SANDBOX_KINDS: Final = frozenset({SCRIPT, TIMEOUT, ABORTED, SANDBOX, STALLED})

#: Kinds that describe one tool call. These are raised as exceptions inside the
#: script, so ``try``/``except`` can branch on them.
TOOL_KINDS: Final = frozenset(
    {UNKNOWN_TOOL, INVALID_INPUT, TOOL_FAILURE, INVALID_OUTPUT, HOST_UNAVAILABLE}
)

#: What the model should try next, per kind. The category exists to change
#: behavior, not just to label.
_REMEDY: Final = {
    SCRIPT: (
        "The script raised or failed to compile. The traceback carries a line "
        "number in your own source; fix that line and run again."
    ),
    TIMEOUT: (
        "The deadline expired and the process was killed. Narrow the work: fetch "
        "less, split the job across calls, or filter in steps rather than one pass."
    ),
    ABORTED: "The run was cancelled. Nothing was written to the store.",
    SANDBOX: (
        "The sandbox process failed, not your script. Retry once; if it persists, "
        "report it rather than rewriting working code."
    ),
    STALLED: (
        "The script is waiting on a result that can never arrive: no tool call is "
        "outstanding and no timer can fire. You awaited something that will never "
        "settle -- await a tool call, or stop awaiting."
    ),
    UNKNOWN_TOOL: (
        "That tool is not in the sandbox. Check the declared tools for the exact "
        "name; tool names are exposed as identifiers with non-identifier "
        "characters replaced by underscore."
    ),
    INVALID_INPUT: (
        "The tool was found but the arguments were wrong. Re-read its declared "
        "parameters and call it again with a corrected payload; do not retry the "
        "identical call."
    ),
    TOOL_FAILURE: (
        "The tool ran and declined. Read what it said and change your approach; "
        "repeating the same call will fail the same way."
    ),
    INVALID_OUTPUT: (
        "The tool produced a result that cannot be returned as JSON. Narrow the "
        "call (fewer records, a summary, a filter) so the value is plain data."
    ),
    HOST_UNAVAILABLE: (
        "The host bridge is not wired up for this session. Work with what the "
        "declarations expose instead."
    ),
}


def remedy_for(kind: str) -> str:
    """Return the recovery hint for a diagnostic kind."""
    return _REMEDY.get(kind, _REMEDY[SANDBOX])


class CodemodeError(Exception):
    """Base for every failure that crosses the sandbox boundary."""

    kind: str = SANDBOX

    def diagnostic(self) -> dict[str, Any]:
        """Project into a model-facing diagnostic dict."""
        return {"kind": self.kind, "message": remedy_for(self.kind)}


class ToolError(CodemodeError):
    """A tool's own failure, split into a model-safe message and a private detail.

    Only ``message`` is forwarded into the sandbox. ``detail`` is for host logs
    and is never serialized across the protocol boundary.
    """

    def __init__(
        self, message: str, *, detail: str | None = None, kind: str = TOOL_FAILURE
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.kind = kind

    def diagnostic(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message}


# The names below intentionally omit an `Error` suffix (N818). They are not
# internal identifiers: a model script writes `except UnknownTool:` to branch on
# a wrong tool name differently from a tool that declined, and the shorter name
# is what the model-facing instructions document. Suffixing them would push a
# convention onto the caller for the sake of the callee.


class UnknownTool(ToolError):  # noqa: N818 - see above
    def __init__(self, name: str) -> None:
        super().__init__(f"Unknown tool: {name}", kind=UNKNOWN_TOOL)


class InvalidInput(ToolError):  # noqa: N818 - see above
    def __init__(self, name: str, *, detail: str | None = None) -> None:
        super().__init__(f"Invalid arguments for tool: {name}", detail=detail, kind=INVALID_INPUT)


class InvalidOutput(ToolError):  # noqa: N818 - see above
    def __init__(self, what: str, *, detail: str | None = None) -> None:
        super().__init__(
            f"{what} is not JSON data: {detail or 'no JSON representation'}",
            detail=detail,
            kind=INVALID_OUTPUT,
        )


class HostUnavailable(ToolError):  # noqa: N818 - see above
    def __init__(self, detail: str | None = None) -> None:
        super().__init__(
            "The host bridge is not available in this session",
            detail=detail,
            kind=HOST_UNAVAILABLE,
        )


class ScriptError(CodemodeError):
    """The script failed to compile or raised. Carries model-safe text."""

    kind = SCRIPT

    def __init__(self, message: str, *, stack: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.stack = stack

    def diagnostic(self) -> dict[str, Any]:
        return {"kind": SCRIPT, "message": self.message, "stack": self.stack}


class SandboxError(CodemodeError):
    """The sandbox process or transport failed."""


class ScriptAborted(CodemodeError):  # noqa: N818 - see above
    kind = ABORTED


class ScriptStalled(CodemodeError):  # noqa: N818 - see above
    """Raised inside the worker when the script provably cannot make progress."""

    kind = STALLED

    def diagnostic(self) -> dict[str, Any]:
        return {"kind": STALLED, "message": remedy_for(STALLED), "stack": None}


class ScriptTimeout(CodemodeError):  # noqa: N818 - see above
    kind = TIMEOUT


#: Maps a script-visible exception class to the kind recorded for its tool
#: call. The script raises these by name; the worker catches them per call.
_EXCEPTION_KINDS: Final = {
    "ToolError": TOOL_FAILURE,
    "UnknownTool": UNKNOWN_TOOL,
    "InvalidInput": INVALID_INPUT,
    "InvalidOutput": INVALID_OUTPUT,
    "HostUnavailable": HOST_UNAVAILABLE,
    "ScriptTimeout": TIMEOUT,
    "ScriptAborted": ABORTED,
    "SandboxError": SANDBOX,
    "ScriptError": SCRIPT,
}


def kind_for_exception(name: str) -> str:
    """Return the diagnostic kind for a script-visible exception class name.

    Unrecognized names are sanitized to :data:`SANDBOX`: a name the sandbox
    does not know is either a defect or something the model raised on purpose,
    and neither should leak its text.
    """
    return _EXCEPTION_KINDS.get(name, SANDBOX)


def to_diagnostic(error: BaseException) -> dict[str, Any]:
    """Project a host-side exception into a model-facing diagnostic dict."""
    return error.diagnostic()  # ty:ignore[unresolved-attribute]
