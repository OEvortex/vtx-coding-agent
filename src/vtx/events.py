import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from .core.types import AssistantMessage, FileChanges, StopReason, ToolResultMessage, Usage
from .permissions import ApprovalResponse, AskUserOption, AskUserResponse

# =================================================================================================
# Agent Lifecycle Events
# =================================================================================================


@dataclass
class SessionStartEvent:
    type: Literal["session_start"] = "session_start"
    session_id: str = ""
    cwd: str = ""


@dataclass
class SessionEndEvent:
    type: Literal["session_end"] = "session_end"
    session_id: str = ""
    cwd: str = ""


@dataclass
class AgentStartEvent:
    type: Literal["agent_start"] = "agent_start"


@dataclass
class AgentEndEvent:
    type: Literal["agent_end"] = "agent_end"
    stop_reason: StopReason = StopReason.STOP
    total_turns: int = 0
    total_usage: Usage | None = None
    # Goal bookkeeping for headless / status rendering. Populated by
    # the loop when a goal was active during the run; None otherwise.
    goal_status: str | None = None
    goal_reason: str | None = None


# =================================================================================================
# Turn Lifecycle Events
# =================================================================================================


@dataclass
class TurnStartEvent:
    type: Literal["turn_start"] = "turn_start"
    turn: int = 0


@dataclass
class TurnEndEvent:
    type: Literal["turn_end"] = "turn_end"
    turn: int = 0
    assistant_message: AssistantMessage | None = None
    tool_results: list[ToolResultMessage] = field(default_factory=list)
    stop_reason: StopReason = StopReason.STOP
    tool_call_count: int = 0


# =================================================================================================
# Content Streaming Events
# =================================================================================================


@dataclass
class ThinkingStartEvent:
    type: Literal["thinking_start"] = "thinking_start"


@dataclass
class ThinkingDeltaEvent:
    type: Literal["thinking_delta"] = "thinking_delta"
    delta: str = ""


@dataclass
class ThinkingEndEvent:
    type: Literal["thinking_end"] = "thinking_end"
    thinking: str = ""
    signature: str | None = None


@dataclass
class TextStartEvent:
    type: Literal["text_start"] = "text_start"


@dataclass
class TextDeltaEvent:
    type: Literal["text_delta"] = "text_delta"
    delta: str = ""


@dataclass
class TextEndEvent:
    type: Literal["text_end"] = "text_end"
    text: str = ""


# =================================================================================================
# Tool Events
# =================================================================================================


@dataclass
class ToolStartEvent:
    type: Literal["tool_start"] = "tool_start"
    tool_call_id: str = ""
    tool_name: str = ""


@dataclass
class ToolArgsDeltaEvent:
    type: Literal["tool_args_delta"] = "tool_args_delta"
    tool_call_id: str = ""
    delta: str = ""


@dataclass
class ToolArgsTokenUpdateEvent:
    type: Literal["tool_args_token_update"] = "tool_args_token_update"
    tool_call_id: str = ""
    tool_name: str = ""
    token_count: int = 0


@dataclass
class ToolEndEvent:
    type: Literal["tool_end"] = "tool_end"
    tool_call_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    display: str = ""  # Formatted display string from tool.format_call()


@dataclass
class ToolResultEvent:
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str = ""
    tool_name: str = ""
    result: ToolResultMessage | None = None
    file_changes: FileChanges | None = None


@dataclass
class ToolApprovalEvent:
    type: Literal["tool_approval"] = "tool_approval"
    tool_call_id: str = ""
    tool_name: str = ""
    display: str = ""
    future: asyncio.Future[ApprovalResponse] | None = None


@dataclass
class AskUserEvent:
    """Yielded when the agent invokes the ``ask_user`` tool.

    The UI is expected to display the question to the user, collect an
    answer, and set the future's result. The turn runner awaits the
    future and feeds the response back to the agent as a normal tool
    result. ``options`` is empty when the question is open-ended and
    only free text is accepted.
    """

    type: Literal["ask_user"] = "ask_user"
    tool_call_id: str = ""
    question: str = ""
    header: str = ""
    options: list[AskUserOption] = field(default_factory=list)
    multi_select: bool = False
    future: asyncio.Future[AskUserResponse] | None = None


# =================================================================================================
# Compaction Events
# =================================================================================================


@dataclass
class CompactionStartEvent:
    type: Literal["compaction_start"] = "compaction_start"


@dataclass
class CompactionEndEvent:
    type: Literal["compaction_end"] = "compaction_end"
    tokens_before: int = 0
    tokens_after: int = 0
    aborted: bool = False
    reason: str = ""  # why compaction aborted, empty on success


# =================================================================================================
# Other Events
# =================================================================================================


@dataclass
class RetryEvent:
    type: Literal["retry"] = "retry"
    attempt: int = 0
    total_attempts: int = 3
    delay: float = 0.0
    error: str = ""


@dataclass
class ErrorEvent:
    type: Literal["error"] = "error"
    error: str = ""


@dataclass
class WarningEvent:
    type: Literal["warning"] = "warning"
    warning: str = ""


@dataclass
class InterruptedEvent:
    type: Literal["interrupted"] = "interrupted"
    message: str = "Interrupted by user"


# =================================================================================================
# Background Task Events
# =================================================================================================


@dataclass
class BackgroundTaskCompletedEvent:
    """Yielded between parent turns when a background sub-agent finishes.

    Carries an explicit ``notification_tag`` so consumers can
    distinguish a system notification from a real user message
    (anthropics/claude-code#35610). The parent agent loop also
    appends a synthetic ``UserMessage`` to the session tagged with
    :data:`vtx.tools.background.BACKGROUND_NOTIFICATION_TAG` so the
    model sees the completion before the next turn starts. The event
    itself is yielded to the UI for status affordances only.

    The notification is delivered at most once per task — see
    :meth:`vtx.tools.background.BackgroundTaskManager.drain_completed`.
    """

    type: Literal["background_task_completed"] = "background_task_completed"
    task_id: str = ""
    description: str = ""
    subagent_type: str = ""
    status: Literal["completed", "error", "cancelled"] = "completed"
    summary: str = ""
    turns: int = 0
    total_tokens: int = 0
    notification_tag: str = "vtx:background-task-completion"


# =================================================================================================
# Goal Events
# =================================================================================================


@dataclass
class GoalStartEvent:
    """Yielded once when a new goal is set (and a turn begins)."""

    type: Literal["goal_start"] = "goal_start"
    objective: str = ""
    max_turns_override: int | None = None


@dataclass
class GoalEvaluatingEvent:
    """Yielded between turns while the evaluator decides."""

    type: Literal["goal_evaluating"] = "goal_evaluating"
    turns_evaluated: int = 0


@dataclass
class GoalContinueEvent:
    """Yielded when the evaluator says the goal is not yet met.

    The agent loop injects a synthetic user message containing ``reason``
    so the next turn starts with that guidance.
    """

    type: Literal["goal_continue"] = "goal_continue"
    reason: str = ""
    turns_evaluated: int = 0


@dataclass
class GoalAchievedEvent:
    """Yielded when the evaluator says the goal is met; the loop ends."""

    type: Literal["goal_achieved"] = "goal_achieved"
    reason: str = ""
    turns_evaluated: int = 0
    tokens_used: int = 0


@dataclass
class GoalBudgetLimitedEvent:
    """Yielded when the run hits the goal's turn cap; the loop ends."""

    type: Literal["goal_budget_limited"] = "goal_budget_limited"
    turns_evaluated: int = 0
    tokens_used: int = 0
    max_turns: int = 0


@dataclass
class GoalClearedEvent:
    """Yielded when the user explicitly clears or replaces a goal."""

    type: Literal["goal_cleared"] = "goal_cleared"
    reason: str = ""


@dataclass
class GoalPausedEvent:
    """Yielded when the user pauses an active goal (e.g. interrupt)."""

    type: Literal["goal_paused"] = "goal_paused"


@dataclass
class GoalResumedEvent:
    """Yielded when the user resumes a paused goal."""

    type: Literal["goal_resumed"] = "goal_resumed"


# =================================================================================================
# Union Types
# =================================================================================================

# Events yielded by run_single_turn (turn.py)
StreamEvent = (
    ThinkingStartEvent
    | ThinkingDeltaEvent
    | ThinkingEndEvent
    | TextStartEvent
    | TextDeltaEvent
    | TextEndEvent
    | ToolStartEvent
    | ToolArgsDeltaEvent
    | ToolArgsTokenUpdateEvent
    | ToolEndEvent
    | ToolResultEvent
    | ToolApprovalEvent
    | AskUserEvent
    | RetryEvent
    | TurnEndEvent
    | ErrorEvent
    | WarningEvent
    | InterruptedEvent
    | BackgroundTaskCompletedEvent
)

# All events yielded by Agent.run() (loop.py)
Event = (
    SessionStartEvent
    | SessionEndEvent
    | AgentStartEvent
    | AgentEndEvent
    | TurnStartEvent
    | CompactionStartEvent
    | CompactionEndEvent
    | StreamEvent
    | GoalStartEvent
    | GoalEvaluatingEvent
    | GoalContinueEvent
    | GoalAchievedEvent
    | GoalBudgetLimitedEvent
    | GoalClearedEvent
    | GoalPausedEvent
    | GoalResumedEvent
)
