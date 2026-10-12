"""The public event contract every plugin handler is called against.

The dispatch mode is part of an event's contract, so it is declared here once.
Observational events are ``serial``: handlers run in registration order and their
results are collected. Blocking events are ``serial`` too, but their results are
merged by :func:`merge_results`, where the first ``block`` wins and ``args`` and
``output`` chain through the handlers that follow.

A handler written ``(event, payload)`` receives the event name plus a typed event
object built by :func:`build_event_object`; one written ``(event, payload, ctx)``
also receives the plugin's own context. That two-shape convention is the one the
extension API has always used, and it is preserved here so a handler written
against the old API runs unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from vtx.plugins.events import DispatchMode

# Public event names an extension can subscribe to. Mirrors the vtx event stream
# plus blocking points. Keep this set in sync with the docstring above.
SESSION_START = "session_start"
SESSION_END = "session_end"
SESSION_INFO_CHANGED = "session_info_changed"
SESSION_BEFORE_SWITCH = "session_before_switch"
SESSION_BEFORE_FORK = "session_before_fork"
SESSION_BEFORE_COMPACT = "session_before_compact"
SESSION_COMPACT = "session_compact"
SESSION_COMPACT_FAILED = "session_compact_failed"
SESSION_SHUTDOWN = "session_shutdown"
SESSION_BEFORE_TREE = "session_before_tree"
SESSION_TREE = "session_tree"
AGENT_START = "agent_start"
AGENT_END = "agent_end"
AGENT_SETTLED = "agent_settled"
TURN_START = "turn_start"
TURN_END = "turn_end"
MESSAGE_START = "message_start"
MESSAGE_UPDATE = "message_update"
MESSAGE_END = "message_end"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
PERMISSION_REQUEST = "permission_request"
PERMISSION_DENIED = "permission_denied"
TOOL_EXECUTION_START = "tool_execution_start"
TOOL_EXECUTION_UPDATE = "tool_execution_update"
TOOL_EXECUTION_END = "tool_execution_end"
COMPACTION_START = "compaction_start"
COMPACTION_END = "compaction_end"
# Retired with the refinement subsystem: nothing emits these, and nothing
# listens. Kept so an extension that subscribes to them still loads -- the
# event bus would otherwise raise on an unknown name at import time.
SESSION_BEFORE_REFINE = "session_before_refine"
REFINE_COMPLETE = "refine_complete"
# Agent lifecycle events (re-exported from vtx.agents for convenience).
# The constants live in vtx.agents; importing them here avoids a cycle
# at the call sites that import from vtx.extensions.
AGENT_ACTIVATED = "agent_activated"
AGENT_CHANGED = "agent_changed"
TOOL_GROUP_CHANGED = "tool_group_changed"
# Additional lifecycle events
RESOURCES_DISCOVER = "resources_discover"
CONTEXT = "context"
BEFORE_PROVIDER_REQUEST = "before_provider_request"
BEFORE_PROVIDER_HEADERS = "before_provider_headers"
AFTER_PROVIDER_RESPONSE = "after_provider_response"
BEFORE_AGENT_START = "before_agent_start"
MODEL_SELECT = "model_select"
THINKING_LEVEL_SELECT = "thinking_level_select"
USER_BASH = "user_bash"
INPUT = "input"

ALL_EVENTS: tuple[str, ...] = (
    SESSION_START,
    SESSION_END,
    SESSION_INFO_CHANGED,
    SESSION_BEFORE_SWITCH,
    SESSION_BEFORE_FORK,
    SESSION_BEFORE_COMPACT,
    SESSION_COMPACT,
    SESSION_COMPACT_FAILED,
    SESSION_SHUTDOWN,
    SESSION_BEFORE_TREE,
    SESSION_TREE,
    AGENT_START,
    AGENT_END,
    AGENT_SETTLED,
    TURN_START,
    TURN_END,
    MESSAGE_START,
    MESSAGE_UPDATE,
    MESSAGE_END,
    TOOL_CALL,
    TOOL_RESULT,
    PERMISSION_REQUEST,
    PERMISSION_DENIED,
    TOOL_EXECUTION_START,
    TOOL_EXECUTION_UPDATE,
    TOOL_EXECUTION_END,
    COMPACTION_START,
    COMPACTION_END,
    SESSION_BEFORE_REFINE,
    REFINE_COMPLETE,
    AGENT_ACTIVATED,
    AGENT_CHANGED,
    TOOL_GROUP_CHANGED,
    RESOURCES_DISCOVER,
    CONTEXT,
    BEFORE_PROVIDER_REQUEST,
    BEFORE_PROVIDER_HEADERS,
    AFTER_PROVIDER_RESPONSE,
    BEFORE_AGENT_START,
    MODEL_SELECT,
    THINKING_LEVEL_SELECT,
    USER_BASH,
    INPUT,
)


# --------------------------------------------------------------------------
# Typed event objects
# --------------------------------------------------------------------------


class SourceInfo:
    """Provenance metadata for an extension registration."""

    path: str
    source: str
    scope: str = "temporary"
    origin: str = "top-level"
    base_dir: str | None = None


# =============================================================================
# Event payload types
# =============================================================================


@dataclass
class SessionInfoChangedEvent:
    type: Literal["session_info_changed"] = "session_info_changed"
    name: str | None = None


@dataclass
class SessionBeforeSwitchEvent:
    type: Literal["session_before_switch"] = "session_before_switch"
    reason: str = "new"
    target_session_file: str | None = None


@dataclass
class SessionBeforeForkEvent:
    type: Literal["session_before_fork"] = "session_before_fork"
    entry_id: str = ""
    position: str = "at"


@dataclass
class SessionBeforeCompactEvent:
    type: Literal["session_before_compact"] = "session_before_compact"
    tokens_before: int = 0
    reason: str = "manual"
    will_retry: bool = False
    aborted: bool = False


@dataclass
class SessionCompactEvent:
    type: Literal["session_compact"] = "session_compact"
    tokens_before: int = 0
    tokens_after: int = 0
    reason: str = "manual"
    will_retry: bool = False
    from_extension: bool = False


@dataclass
class SessionCompactFailedEvent:
    type: Literal["session_compact_failed"] = "session_compact_failed"
    reason: str = "manual"
    error_message: str | None = None
    aborted: bool = False
    will_retry: bool = False
    from_extension: bool = False


@dataclass
class SessionShutdownEvent:
    type: Literal["session_shutdown"] = "session_shutdown"
    reason: str = "quit"
    target_session_file: str | None = None


@dataclass
class SessionBeforeTreeEvent:
    type: Literal["session_before_tree"] = "session_before_tree"
    target_id: str = ""
    signal: Any = None


@dataclass
class SessionTreeEvent:
    type: Literal["session_tree"] = "session_tree"
    new_leaf_id: str | None = None
    old_leaf_id: str | None = None


@dataclass
class AgentSettledEvent:
    type: Literal["agent_settled"] = "agent_settled"


@dataclass
class MessageStartEvent:
    type: Literal["message_start"] = "message_start"
    message: Any = None


@dataclass
class MessageUpdateEvent:
    type: Literal["message_update"] = "message_update"
    message: Any = None


@dataclass
class MessageEndEvent:
    type: Literal["message_end"] = "message_end"
    message: Any = None


@dataclass
class ToolExecutionStartEvent:
    type: Literal["tool_execution_start"] = "tool_execution_start"
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolExecutionUpdateEvent:
    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    partial_result: Any = None


@dataclass
class ToolExecutionEndEvent:
    type: Literal["tool_execution_end"] = "tool_execution_end"
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    is_error: bool = False


@dataclass
class ResourcesDiscoverEvent:
    type: Literal["resources_discover"] = "resources_discover"
    cwd: str = ""
    reason: str = "startup"


@dataclass
class ContextEvent:
    type: Literal["context"] = "context"
    messages: list[Any] = field(default_factory=list)


@dataclass
class BeforeProviderRequestEvent:
    type: Literal["before_provider_request"] = "before_provider_request"
    payload: Any = None


@dataclass
class BeforeProviderHeadersEvent:
    type: Literal["before_provider_headers"] = "before_provider_headers"
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class AfterProviderResponseEvent:
    type: Literal["after_provider_response"] = "after_provider_response"
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class BeforeAgentStartEvent:
    type: Literal["before_agent_start"] = "before_agent_start"
    prompt: str = ""
    images: list[Any] = field(default_factory=list)
    system_prompt: str = ""
    system_prompt_options: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelSelectEvent:
    type: Literal["model_select"] = "model_select"
    model: str = ""
    previous_model: str | None = None
    source: str = "set"


@dataclass
class ThinkingLevelSelectEvent:
    type: Literal["thinking_level_select"] = "thinking_level_select"
    level: str = "high"
    previous_level: str = "high"


@dataclass
class UserBashEvent:
    type: Literal["user_bash"] = "user_bash"
    command: str = ""
    exclude_from_context: bool = False
    cwd: str = ""


@dataclass
class InputEvent:
    type: Literal["input"] = "input"
    text: str = ""
    images: list[Any] = field(default_factory=list)
    source: str = "interactive"
    streaming_behavior: str | None = None


# =============================================================================
# Result types
# =============================================================================


@dataclass
class ToolCallEventResult:
    block: bool = False
    reason: str | None = None
    terminate: bool = False
    args: dict[str, Any] | None = None


@dataclass
class ToolResultEventResult:
    content: list[Any] | None = None
    details: Any = None
    is_error: bool | None = None
    output: str | None = None


@dataclass
class UserBashEventResult:
    operations: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


@dataclass
class InputEventResult:
    action: str = "continue"
    text: str | None = None
    images: list[Any] | None = None


@dataclass
class ContextEventResult:
    messages: list[Any] | None = None


@dataclass
class SessionBeforeSwitchResult:
    cancel: bool = False


@dataclass
class SessionBeforeForkResult:
    cancel: bool = False
    skip_conversation_restore: bool = False


@dataclass
class SessionBeforeCompactResult:
    cancel: bool = False
    compaction: dict[str, Any] | None = None


@dataclass
class SessionBeforeTreeResult:
    cancel: bool = False
    summary: str | None = None
    custom_instructions: str | None = None
    replace_instructions: bool = False
    label: str | None = None


@dataclass
class MessageEndEventResult:
    message: Any = None


@dataclass
class BeforeAgentStartEventResult:
    message: dict[str, Any] | None = None
    system_prompt: str | None = None


@dataclass
class ResourcesDiscoverResult:
    skill_paths: list[str] = field(default_factory=list)
    prompt_paths: list[str] = field(default_factory=list)
    theme_paths: list[str] = field(default_factory=list)


@dataclass
class SessionStartEvent:
    type: Literal["session_start"] = "session_start"
    cwd: str = ""
    session_id: str = ""


@dataclass
class SessionEndEvent:
    type: Literal["session_end"] = "session_end"
    cwd: str = ""
    session_id: str = ""


@dataclass
class CompactionStartEvent:
    type: Literal["compaction_start"] = "compaction_start"
    tokens_before: int = 0


@dataclass
class CompactionEndEvent:
    type: Literal["compaction_end"] = "compaction_end"
    tokens_before: int = 0
    tokens_after: int = 0
    aborted: bool = False
    reason: str = ""
    summary: str = ""


@dataclass
class AgentActivatedEvent:
    type: Literal["agent_activated"] = "agent_activated"
    agent: str = ""


@dataclass
class AgentChangedEvent:
    type: Literal["agent_changed"] = "agent_changed"
    previous: str | None = None
    current: str | None = None


@dataclass
class ToolGroupChangedEvent:
    type: Literal["tool_group_changed"] = "tool_group_changed"
    agent: str | None = None
    group: str | None = None


@dataclass
class ToolCallEvent:
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    tool: Any = None


@dataclass
class ToolResultEvent:
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    content: list[Any] | None = None
    details: Any = None
    is_error: bool = False
    output: str | None = None


# --------------------------------------------------------------------------
# Event object helpers
# --------------------------------------------------------------------------

_EVENT_CLASS_MAP: dict[str, type[Any]] | None = None

#: A handler may return a dict carrying ``block``; that is what stops a tool
#: from running. The other two are the chained-rewrite keys.
BLOCK = "block"
REASON = "reason"
ARGS = "args"
OUTPUT = "output"


def event_class_map() -> dict[str, type[Any]]:
    """Event name to its typed payload class."""
    global _EVENT_CLASS_MAP
    if _EVENT_CLASS_MAP is None:
        from vtx.core.events import AgentEndEvent, AgentStartEvent, TurnEndEvent, TurnStartEvent

        _EVENT_CLASS_MAP = {
            SESSION_START: SessionStartEvent,
            SESSION_END: SessionEndEvent,
            SESSION_INFO_CHANGED: SessionInfoChangedEvent,
            SESSION_BEFORE_SWITCH: SessionBeforeSwitchEvent,
            SESSION_BEFORE_FORK: SessionBeforeForkEvent,
            SESSION_BEFORE_COMPACT: SessionBeforeCompactEvent,
            SESSION_COMPACT: SessionCompactEvent,
            SESSION_COMPACT_FAILED: SessionCompactFailedEvent,
            SESSION_SHUTDOWN: SessionShutdownEvent,
            SESSION_BEFORE_TREE: SessionBeforeTreeEvent,
            SESSION_TREE: SessionTreeEvent,
            AGENT_START: AgentStartEvent,
            AGENT_END: AgentEndEvent,
            AGENT_SETTLED: AgentSettledEvent,
            TURN_START: TurnStartEvent,
            TURN_END: TurnEndEvent,
            MESSAGE_START: MessageStartEvent,
            MESSAGE_UPDATE: MessageUpdateEvent,
            MESSAGE_END: MessageEndEvent,
            TOOL_CALL: ToolCallEvent,
            TOOL_RESULT: ToolResultEvent,
            TOOL_EXECUTION_START: ToolExecutionStartEvent,
            TOOL_EXECUTION_UPDATE: ToolExecutionUpdateEvent,
            TOOL_EXECUTION_END: ToolExecutionEndEvent,
            COMPACTION_START: CompactionStartEvent,
            COMPACTION_END: CompactionEndEvent,
            AGENT_ACTIVATED: AgentActivatedEvent,
            AGENT_CHANGED: AgentChangedEvent,
            TOOL_GROUP_CHANGED: ToolGroupChangedEvent,
            RESOURCES_DISCOVER: ResourcesDiscoverEvent,
            CONTEXT: ContextEvent,
            BEFORE_PROVIDER_REQUEST: BeforeProviderRequestEvent,
            BEFORE_PROVIDER_HEADERS: BeforeProviderHeadersEvent,
            AFTER_PROVIDER_RESPONSE: AfterProviderResponseEvent,
            BEFORE_AGENT_START: BeforeAgentStartEvent,
            MODEL_SELECT: ModelSelectEvent,
            THINKING_LEVEL_SELECT: ThinkingLevelSelectEvent,
            USER_BASH: UserBashEvent,
            INPUT: InputEvent,
        }
    return _EVENT_CLASS_MAP


def build_event_object(event: str, payload: dict[str, Any]) -> Any:
    """Wrap ``payload`` in its typed class, or return the mapping unchanged."""
    cls = event_class_map().get(event)
    if cls is None:
        return payload
    try:
        return cls(**payload)
    except Exception:
        return payload


#: The events whose handler results the caller acts on: the first ``block``
#: stops the work, and ``args`` and ``output`` chain through what follows.
BLOCKING_EVENTS = frozenset({TOOL_CALL, TOOL_RESULT, USER_BASH, INPUT})

#: Every event's dispatch mode. All are ``serial`` so registration order stays
#: meaningful, which is what a blocking handler and an observer both expect.
EVENT_MODES: dict[str, DispatchMode] = {name: DispatchMode.SERIAL for name in ALL_EVENTS}


def merge_results(results: list[Any]) -> dict[str, Any]:
    """Fold handler returns into one decision dict.

    The first ``block`` wins and stops the fold. ``args`` and ``output`` are
    merged so a later handler chains off them, exactly as the event bus always
    behaved.
    """
    merged: dict[str, Any] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        if result.get(BLOCK):
            merged[BLOCK] = True
            if REASON in result:
                merged[REASON] = result[REASON]
            return merged
        if ARGS in result:
            merged[ARGS] = result[ARGS]
        if OUTPUT in result:
            merged[OUTPUT] = result[OUTPUT]
        merged.update({k: v for k, v in result.items() if k not in (BLOCK, ARGS, OUTPUT)})
    return merged
