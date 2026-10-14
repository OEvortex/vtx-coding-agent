"""Shared foundations: agent events, the permission gate, notifications, and the
scratchpad directory.

Message and event *types* live in :mod:`vtx.protocol` and tracing lives in
:mod:`vtx.telemetry`; both are leaves that this package depends on.

The event, permission, and scratchpad names in :data:`__all__` are re-exported
lazily through :func:`__getattr__`. Importing this package used to pull that whole
graph into the process, which is what made ``from vtx.core.config import config``
pay ~300ms of imports that ``config`` itself does not use. ``notify`` stays eager
because its name collides with its own submodule (see the comment below).
"""

import importlib
from typing import TYPE_CHECKING, Any

# Imported eagerly, unlike the rest of this package's re-exports. ``notify`` is
# both a submodule and an exported name, so a lazy ``__getattr__`` cannot always
# resolve it: when ``vtx.core.notify`` is imported as a submodule first (the TUI
# does exactly that) the import system binds the *module* onto this package's
# ``__dict__`` and ``__getattr__`` never runs. This module is ~2ms of stdlib.
from .notify import NotificationEvent, notify

if TYPE_CHECKING:
    from .events import (
        AgentEndEvent,
        AgentStartEvent,
        AskUserEvent,
        BackgroundTaskCompletedEvent,
        CompactionEndEvent,
        CompactionProgressEvent,
        CompactionStartEvent,
        ErrorEvent,
        HostNoticeEvent,
        InterruptedEvent,
        RetryEvent,
        SessionEndEvent,
        SessionStartEvent,
        TextDeltaEvent,
        TextEndEvent,
        TextStartEvent,
        ThinkingDeltaEvent,
        ThinkingEndEvent,
        ThinkingStartEvent,
        ToolApprovalEvent,
        ToolArgsDeltaEvent,
        ToolArgsTokenUpdateEvent,
        ToolEndEvent,
        ToolOutputDeltaEvent,
        ToolResultEvent,
        ToolStartEvent,
        TurnEndEvent,
        TurnStartEvent,
        WarningEvent,
    )
    from .permissions import (
        ApprovalResponse,
        AskUserAnswer,
        AskUserOption,
        AskUserQuestion,
        AskUserResponse,
        PermissionDecision,
        _is_safe_bash_command,
        check_permission,
    )
    from .scratchpad import get_scratchpad_dir, init_scratchpad, is_scratchpad_path

# Kept in the historical import order so a name present in more than one of
# these still resolves to the same object as the eager re-exports did. ``notify``
# is in here only as documentation: it is already bound above, so ``__getattr__``
# never reaches this loop for it.
_SOURCES = ("vtx.core.events", "vtx.core.notify", "vtx.core.permissions", "vtx.core.scratchpad")


def __getattr__(name: str) -> Any:
    for module_name in _SOURCES:
        module = importlib.import_module(module_name)
        if (value := getattr(module, name, None)) is not None:
            # Cache into ``__dict__``. Without this the import system binds the
            # submodule of the same name onto this package (``vtx.core.notify``
            # is both a module and a re-exported function) and the second lookup
            # would hand back the module.
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "AgentEndEvent",
    "AgentStartEvent",
    "ApprovalResponse",
    "AskUserAnswer",
    "AskUserEvent",
    "AskUserOption",
    "AskUserQuestion",
    "AskUserResponse",
    "BackgroundTaskCompletedEvent",
    "CompactionEndEvent",
    "CompactionProgressEvent",
    "CompactionStartEvent",
    "ErrorEvent",
    "HostNoticeEvent",
    "InterruptedEvent",
    "NotificationEvent",
    "PermissionDecision",
    "RetryEvent",
    "SessionEndEvent",
    "SessionStartEvent",
    "TextDeltaEvent",
    "TextEndEvent",
    "TextStartEvent",
    "ThinkingDeltaEvent",
    "ThinkingEndEvent",
    "ThinkingStartEvent",
    "ToolApprovalEvent",
    "ToolArgsDeltaEvent",
    "ToolArgsTokenUpdateEvent",
    "ToolEndEvent",
    "ToolOutputDeltaEvent",
    "ToolResultEvent",
    "ToolStartEvent",
    "TurnEndEvent",
    "TurnStartEvent",
    "WarningEvent",
    "_is_safe_bash_command",
    "check_permission",
    "get_scratchpad_dir",
    "init_scratchpad",
    "is_scratchpad_path",
    "notify",
]
