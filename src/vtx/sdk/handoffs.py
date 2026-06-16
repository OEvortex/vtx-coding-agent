"""
Handoffs — the multi-agent delegation primitive.

A handoff is a callable that, when invoked, transfers control of the
run to a target agent. The target agent receives the full conversation
history and produces the response for the rest of the turn.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..core.types import ToolResult
from ..tools.base import BaseTool

if TYPE_CHECKING:
    from .agent import Agent


@dataclass
class HandoffInputData:
    """The payload passed to an ``input_filter`` function.

    ``input_history`` is the conversation history before the current turn.
    ``pre_handoff_items`` is everything generated before the agent turn where
    the handoff was invoked.
    ``new_items`` is everything generated during the current turn, including
    the handoff call itself.
    """

    input_history: str | list[Any]
    pre_handoff_items: list[Any]
    new_items: list[Any]
    run_context: Any = None


def handoff(
    agent: Agent,
    *,
    tool_name_override: str | None = None,
    tool_description_override: str | None = None,
    on_handoff: Callable[[Any], Any] | None = None,
    input_filter: Callable[[HandoffInputData], HandoffInputData] | None = None,
    input_type: type[BaseModel] | None = None,
) -> Handoff:
    """Create a handoff from the current agent to ``agent``.

    Parameters
    ----------
    agent:
        The target :class:`vtx.sdk.Agent` to delegate to.
    tool_name_override:
        Override the auto-generated tool name (``transfer_to_<agent>``).
    tool_description_override:
        Override the auto-generated tool description.
    on_handoff:
        Optional callback fired the moment the handoff is invoked. Receives
        the SDK ``RunContextWrapper``. May be sync or async.
    input_filter:
        Optional function that transforms the conversation history before
        it is handed to the target agent.
    input_type:
        Optional Pydantic model describing structured input the model can
        pass when calling the handoff tool.
    """
    return Handoff(
        agent=agent,
        tool_name_override=tool_name_override,
        tool_description_override=tool_description_override,
        on_handoff=on_handoff,
        input_filter=input_filter,
        input_type=input_type,
    )


class Handoff(BaseTool):
    """The runtime form of a handoff. Implements :class:`BaseTool` so it can
    be plugged into the agent's tool list.
    """

    target_agent: Agent
    on_handoff_callback: Callable[[Any], Any] | None
    input_filter: Callable[[HandoffInputData], HandoffInputData] | None
    input_type: type[BaseModel] | None

    def __init__(
        self,
        agent: Agent,
        *,
        tool_name_override: str | None = None,
        tool_description_override: str | None = None,
        on_handoff: Callable[[Any], Any] | None = None,
        input_filter: Callable[[HandoffInputData], HandoffInputData] | None = None,
        input_type: type[BaseModel] | None = None,
    ) -> None:
        self.target_agent = agent
        self.tool_name_override = tool_name_override
        self.tool_description_override = tool_description_override
        self.on_handoff_callback = on_handoff
        self.input_filter = input_filter
        self.input_type = input_type

        self.name = tool_name_override or f"transfer_to_{agent.name.lower().replace(' ', '_')}"
        self.description = (
            tool_description_override
            or f"Handoff to the {agent.name!r} agent to handle the request. "
            f"Use this when the user's question is best answered by {agent.name!r}."
        )
        self.mutating = False
        self.tool_icon = "↪"
        self.prompt_guidelines = ()

        # Pydantic params: optional structured input the model can pass.
        if input_type is not None:
            self.params = input_type
        else:
            # The handoff tool takes no required arguments; the model can
            # optionally pass a free-form ``reason`` field.
            from pydantic import create_model

            self.params = create_model(  # type: ignore[call-overload]
                f"HandoffTo{agent.name.title().replace(' ', '')}_Params", reason=(str | None, None)
            )

    def format_call(self, params: BaseModel) -> str:
        reason = getattr(params, "reason", None)
        target = self.target_agent.name
        if reason:
            return f"→ {target} (reason: {reason})"
        return f"→ {target}"

    async def execute(
        self, params: BaseModel, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        """Execute the handoff.

        The actual transfer of control happens in the SDK runner, not here.
        This method's job is to:

        1. Fire the ``on_handoff`` callback (sync or async).
        2. Return a placeholder result. The runner will replace the agent
           for the rest of the run and the user-visible final output is
           the target agent's ``final_output``, not this string.
        """
        if self.on_handoff_callback is not None:
            result = self.on_handoff_callback(None)
            if inspect.isawaitable(result):
                await result

        # The actual handoff is performed by the runner; this tool result
        # is never user-visible because the runner switches agents and the
        # target's output becomes the assistant's reply.
        return ToolResult(success=True, result=f"[handoff-to: {self.target_agent.name}]")


__all__ = ["Handoff", "HandoffInputData", "handoff"]
