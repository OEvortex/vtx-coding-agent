"""``/goal`` command machinery.

A :class:`Goal` is a persisted completion condition that the agent
should keep working toward across turns. The :class:`GoalManager` owns
the active goal and runs an evaluator between turns to decide if the
condition is met.

Design notes:

- The manager holds **state only**. It does not call tools. Between
  turns, the agent loop invokes :meth:`GoalManager.evaluate` with a
  cheap LLM call (the configured ``goal.evaluator_model`` or the
  active default) to get a yes/no decision plus a one-line reason.
- Goal state survives ``--resume``: the active goal is appended to the
  session as a :class:`~vtx.session.GoalEntry` and restored on the
  next ``initialize()`` call.
- Lifecycle states mirror Claude Code / Codex: ``pursuing``,
  ``paused``, ``achieved``, ``unmet``, ``budget_limited``.
- Budget is enforced via an optional ``max_turns_override`` parsed
  from ``or stop after N turns`` clauses in the objective, falling
  back to :attr:`vtx.config.AgentConfig.max_turns`.
"""

from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .core.types import AssistantMessage, Message, TextContent, UserMessage

if TYPE_CHECKING:
    from .llm import BaseProvider
    from .session import Session

log = logging.getLogger("vtx.goal")


# Public event-name constants for extensions / agent_runner. The strings
# are stable; the EventBus in ``vtx.extensions`` is the canonical owner
# of event-name registration but exporting them here keeps the goal
# code self-contained.
GOAL_START = "goal_start"
GOAL_END = "goal_end"
GOAL_PAUSED = "goal_paused"
GOAL_RESUMED = "goal_resumed"

# XML tag the loop wraps synthetic goal-guidance messages in. The
# agent loop injects a ``UserMessage`` carrying the evaluator's
# "why not" verdict between turns so the next turn starts with that
# guidance. The tag also helps the chat renderer recognise and
# de-emphasise (or hide) these system messages if it wants to.
GOAL_TAG = "vtx:goal-guidance"


# Matches the ``or stop after N turns`` / ``or stop after N minutes``
# clause Claude Code documents. Captured groups:
#   group 1: "turns" | "minutes"
#   group 2: numeric value
_BUDGET_CLAUSE_RE = re.compile(r"\bor\s+stop\s+after\s+(\d+)\s+(turns?|minutes?)\b", re.IGNORECASE)


@dataclass
class Goal:
    """A persisted completion condition and its runtime status."""

    objective: str
    status: str = "pursuing"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str | None = None
    turns_evaluated: int = 0
    tokens_used: int = 0
    last_reason: str | None = None
    max_turns_override: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "turns_evaluated": self.turns_evaluated,
            "tokens_used": self.tokens_used,
            "last_reason": self.last_reason,
            "max_turns_override": self.max_turns_override,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Goal:
        return cls(
            objective=str(data.get("objective", "")),
            status=str(data.get("status", "pursuing")),
            created_at=str(data.get("created_at") or datetime.now(UTC).isoformat()),
            completed_at=data.get("completed_at"),
            turns_evaluated=int(data.get("turns_evaluated", 0)),
            tokens_used=int(data.get("tokens_used", 0)),
            last_reason=data.get("last_reason"),
            max_turns_override=(
                int(data["max_turns_override"])
                if data.get("max_turns_override") is not None
                else None
            ),
        )


def parse_budget_clause(objective: str) -> tuple[str, int | None]:
    """Strip a trailing ``or stop after N turns/minutes`` clause from the
    objective. Returns ``(clean_objective, max_turns_override_or_None)``.

    ``minutes`` is converted to a rough turn cap using the configured
    ``agent.max_turns`` heuristic (60 seconds per turn). When the
    override is non-trivial the result is an integer; otherwise None.
    """
    match = _BUDGET_CLAUSE_RE.search(objective)
    if not match:
        return objective.strip(), None
    raw_value = int(match.group(1))
    unit = match.group(2).lower()
    clean = (objective[: match.start()] + objective[match.end() :]).strip()

    if "minute" in unit:
        # Rough conversion: assume ~1 turn / minute for short objectives.
        # The cap is informational; the actual ceiling is the YAML
        # ``agent.max_turns`` knob, and we still warn the user.
        return clean, max(1, raw_value)
    return clean, raw_value


# Evaluator response parsing ---------------------------------------------------


_EVAL_YES_RE = re.compile(r"^\s*(?:yes|achieved|met|complete|done)\s*[:\-]\s*(.+)$", re.IGNORECASE)
_EVAL_NO_RE = re.compile(
    r"^\s*(?:no|not yet|continue|unmet|working on it)\s*[:\-]\s*(.+)$", re.IGNORECASE
)


def parse_evaluator_decision(text: str) -> tuple[bool, str]:
    """Extract a yes/no decision and reason from the evaluator's reply.

    Falls back to the whole trimmed text as the reason. The decision is
    False when the response is ambiguous — safer to keep the agent
    running than to declare success on a misread.
    """
    if not text:
        return False, "evaluator returned empty response"
    first_line = text.strip().splitlines()[0].strip()
    yes_match = _EVAL_YES_RE.match(first_line)
    if yes_match:
        return True, yes_match.group(1).strip()
    no_match = _EVAL_NO_RE.match(first_line)
    if no_match:
        return False, no_match.group(1).strip()
    # Heuristic fallback: if the line starts with a clearly affirmative
    # word we treat it as yes; otherwise no.
    lowered = first_line.lower()
    if lowered.startswith(("yes ", "yes.", "yes!", "achieved", "met ", "complete")):
        return True, first_line
    return False, first_line


# Transcript sampling ---------------------------------------------------------


# How many recent transcript items to feed the evaluator. Keeps the
# evaluator prompt small and cheap; older context is dropped.
_TRANSCRIPT_TAIL = 12


def _format_transcript(messages: list[Message]) -> str:
    """Render a tail of the conversation as plain text for the evaluator."""
    if not messages:
        return "(no messages yet)"
    tail = messages[-_TRANSCRIPT_TAIL:]
    parts: list[str] = []
    for message in tail:
        if isinstance(message, UserMessage):
            content = message.content
            if isinstance(content, str):
                text = content
            else:
                text = "".join(part.text for part in content if isinstance(part, TextContent))
            parts.append(f"USER: {text.strip()[:1000]}")
        elif isinstance(message, AssistantMessage):
            text = "".join(part.text for part in message.content if isinstance(part, TextContent))
            if text:
                parts.append(f"ASSISTANT: {text.strip()[:1000]}")
            tool_calls = [part for part in message.content if not isinstance(part, TextContent)]
            for tc in tool_calls:
                name = getattr(tc, "name", "")
                args = getattr(tc, "arguments", {})
                args_str = str(args)
                if len(args_str) > 200:
                    args_str = args_str[:197] + "..."
                parts.append(f"TOOL_CALL: {name}({args_str})")
    return "\n\n".join(parts)


# Evaluator prompt -------------------------------------------------------------


EVALUATOR_SYSTEM_PROMPT = """You are a goal-completion evaluator for an autonomous agent.

You receive:
1. A completion condition (the goal).
2. A tail of the agent's recent conversation transcript.

Your only job is to decide whether the completion condition is currently satisfied,
based solely on what the agent has surfaced in the transcript. You must not run
any tools, read any files, or make assumptions about state outside the transcript.

Reply with EXACTLY ONE LINE in one of these formats:
  YES: <one short sentence explaining why the condition is now satisfied>
  NO: <one short sentence explaining what is still missing or what the agent should do next>

Rules:
- "YES" means the condition is fully met, not "in progress" or "almost there".
- If the agent's last action was a tool call with no visible result yet, reply NO.
- If the agent has not produced any concrete evidence, reply NO.
- Keep the reason under 25 words.
- Do not include any other text, markdown, or preamble."""


def build_evaluator_user_prompt(objective: str, transcript: str) -> str:
    return (
        f"GOAL:\n{objective.strip()}\n\n"
        f"RECENT TRANSCRIPT (most recent last):\n```\n{transcript}\n```\n\n"
        "Reply with exactly one line: YES: <reason> or NO: <reason>."
    )


# Manager ----------------------------------------------------------------------


@dataclass
class EvaluatorOutcome:
    """The evaluator's response to a single decision request."""

    achieved: bool
    reason: str
    tokens_used: int = 0


class GoalManager:
    """Owns the active :class:`Goal` and orchestrates evaluation between turns.

    The manager is owned by :class:`vtx.runtime.ConversationRuntime` and
    invoked by the agent loop in :mod:`vtx.loop` between ``TURN_END``
    and the next turn. The runtime is responsible for calling
    :meth:`evaluate` (which performs the LLM call) and applying the
    resulting yes/no decision to the loop.
    """

    def __init__(self, *, max_objective_chars: int = 4000, max_turns_default: int = 500) -> None:
        self._max_objective_chars = max_objective_chars
        self._max_turns_default = max_turns_default
        self._goal: Goal | None = None

    # ---- read-only state ------------------------------------------------

    @property
    def goal(self) -> Goal | None:
        return self._goal

    @property
    def active(self) -> bool:
        return self._goal is not None and self._goal.status in ("pursuing", "paused")

    @property
    def max_turns(self) -> int:
        """The effective turn cap for the current run (override or default)."""
        if self._goal is not None and self._goal.max_turns_override:
            return min(self._max_turns_default, self._goal.max_turns_override)
        return self._max_turns_default

    # ---- lifecycle ------------------------------------------------------

    def set_goal(self, objective: str) -> tuple[Goal, str | None]:
        """Replace the current goal with a new pursuing one.

        Returns ``(goal, parse_warning_or_None)`` so the caller can surface
        any budget-clause parse result to the user. Raises :class:`ValueError`
        when the objective is empty or exceeds the configured cap.
        """
        cleaned, max_turns_override = parse_budget_clause(objective)
        if not cleaned:
            raise ValueError("goal objective cannot be empty")
        if len(cleaned) > self._max_objective_chars:
            raise ValueError(f"goal objective exceeds {self._max_objective_chars} characters")
        warning: str | None = None
        if max_turns_override is not None:
            warning = (
                f"Budget: stop after {max_turns_override} turn(s) "
                f"(capped by agent.max_turns={self._max_turns_default})"
            )
        self._goal = Goal(
            objective=cleaned, status="pursuing", max_turns_override=max_turns_override
        )
        return self._goal, warning

    def clear(self) -> Goal | None:
        prior = self._goal
        self._goal = None
        return prior

    def pause(self) -> Goal | None:
        if self._goal is None or self._goal.status != "pursuing":
            return None
        self._goal.status = "paused"
        return self._goal

    def resume(self) -> Goal | None:
        if self._goal is None or self._goal.status != "paused":
            return None
        self._goal.status = "pursuing"
        return self._goal

    # ---- evaluation ----------------------------------------------------

    async def evaluate(
        self, messages: list[Message], *, provider: BaseProvider, model: str
    ) -> EvaluatorOutcome:
        """Ask the configured evaluator whether the condition is met.

        ``provider`` must already be initialised with the target model
        (the runtime swaps in the goal.evaluator_model when present).
        ``messages`` is the active transcript; the manager only inspects
        a tail of it.
        """
        if self._goal is None:
            return EvaluatorOutcome(achieved=False, reason="no active goal")
        transcript = _format_transcript(messages)
        prompt = build_evaluator_user_prompt(self._goal.objective, transcript)

        # The evaluator uses zero tools. We swap the provider's configured
        # model for the evaluator model without mutating persistent state.
        try:
            original_model = provider.config.model
            provider.config.model = model
            response = await _call_evaluator_provider(provider, prompt, EVALUATOR_SYSTEM_PROMPT)
        except Exception as exc:
            log.warning("goal evaluator failed: %s", exc)
            return EvaluatorOutcome(achieved=False, reason=f"evaluator error: {exc}")
        finally:
            with contextlib.suppress(Exception):
                provider.config.model = original_model

        self._goal.turns_evaluated += 1
        achieved, reason = parse_evaluator_decision(response.text)
        self._goal.last_reason = reason

        if achieved:
            self._goal.status = "achieved"
            self._goal.completed_at = datetime.now(UTC).isoformat()
        return EvaluatorOutcome(achieved=achieved, reason=reason, tokens_used=response.tokens)

    def record_turn_tokens(self, tokens: int) -> None:
        if self._goal is not None and tokens:
            self._goal.tokens_used += tokens

    def mark_budget_limited(self) -> None:
        if self._goal is not None and self._goal.status == "pursuing":
            self._goal.status = "budget_limited"
            self._goal.completed_at = datetime.now(UTC).isoformat()

    def mark_unmet(self) -> None:
        if self._goal is not None and self._goal.status == "pursuing":
            self._goal.status = "unmet"
            self._goal.completed_at = datetime.now(UTC).isoformat()

    # ---- persistence ---------------------------------------------------

    def restore_from_session(self, session: Session) -> Goal | None:
        """Walk the session and restore the most recent goal state.

        A ``pursuing`` goal is restored as ``pursuing`` (the loop will
        start evaluating again). ``achieved`` / ``budget_limited`` /
        ``unmet`` goals are restored in their terminal state and kept
        for visibility but do not auto-resume.
        """
        last: Goal | None = None
        for entry in reversed(session.active_entries):
            # Late import to avoid a circular dependency at module load.
            from .session import GoalEntry

            if isinstance(entry, GoalEntry):
                last = Goal.from_dict(entry.goal)
                break
        if last is not None:
            self._goal = last
        return last

    def to_entry_dict(self) -> dict[str, Any]:
        if self._goal is None:
            return {}
        return self._goal.to_dict()


# Provider-side helper --------------------------------------------------------


class _EvaluatorResponse:
    __slots__ = ("text", "tokens")

    def __init__(self, text: str, tokens: int) -> None:
        self.text = text
        self.tokens = tokens


async def _call_evaluator_provider(
    provider: BaseProvider, prompt: str, system_prompt: str
) -> _EvaluatorResponse:
    """Single-shot async evaluator call.

    The provider returns an :class:`LLMStream` (async iterator over
    :class:`~vtx.core.types.StreamPart`). We collect text deltas and
    tally usage, then close the stream so the underlying HTTP session
    is released.
    """
    from .core.types import UserMessage

    messages: list[Message] = [UserMessage(content=prompt)]
    text_parts: list[str] = []
    tokens_used = 0
    stream = await provider.stream(
        messages,
        system_prompt=system_prompt,
        tools=[],  # evaluator must not call tools
    )
    try:
        async for part in stream:
            text = getattr(part, "text", None)
            if text:
                text_parts.append(text)
        usage = getattr(stream, "usage", None)
        if usage is not None:
            tokens_used += getattr(usage, "input_tokens", 0) or 0
            tokens_used += getattr(usage, "output_tokens", 0) or 0
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            with contextlib.suppress(Exception):
                await close()
    return _EvaluatorResponse(text="".join(text_parts).strip(), tokens=tokens_used)


__all__ = [
    "EVALUATOR_SYSTEM_PROMPT",
    "GOAL_END",
    "GOAL_PAUSED",
    "GOAL_RESUMED",
    "GOAL_START",
    "GOAL_TAG",
    "EvaluatorOutcome",
    "Goal",
    "GoalManager",
    "build_evaluator_user_prompt",
    "parse_budget_clause",
    "parse_evaluator_decision",
]
