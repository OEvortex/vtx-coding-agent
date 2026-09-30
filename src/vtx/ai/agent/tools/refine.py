"""The ``refine`` tool: continual-harness refinement for the tool-first mode.

``code_first`` sessions get the same capability from the kernel skill
(``await refine.run()``), because that mode runs every call through the
persistent Python REPL. In ``tool_first`` there is no REPL, so the
capability is exposed as an ordinary tool instead. Both surfaces queue the
*same* turn-boundary request in the RLM host registry, which the parent
agent loop drains (:meth:`vtx.ai.agent.loop.Agent._drain_pending_refinement`).

Refinement never runs mid-turn: ``execute`` only records the request and
returns immediately. The pass runs once the current turn ends, applies its
edits, appends the refinement notice to the session, and the loop resumes the
model on the rebuilt system prompt.
"""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from pydantic import BaseModel, Field

from vtx.ai.agent.tools.base import BaseTool
from vtx.core.types import ToolResult

REFINE_NOTE = (
    "Refinement runs when the current turn ends; applied edits are appended "
    "to your context as a refinement notice and you resume automatically. "
    "Continue working normally."
)

MAX_INSTRUCTIONS_CHARS = 2_000


class RefineParams(BaseModel):
    action: Literal["run", "status"] = Field(
        default="run", description="'run' schedules a refinement, 'status' reports the queue"
    )
    instructions: str | None = Field(
        default=None,
        max_length=MAX_INSTRUCTIONS_CHARS,
        description="Optional focus for this pass, e.g. the failure worth remembering",
    )
    global_: bool = Field(
        default=False,
        description=(
            "Target the cross-session harness store. Only for stable, reusable "
            "lessons; leave false for current-task progress and blockers"
        ),
    )


def _json(payload: dict[str, object], *, success: bool = True) -> ToolResult:
    return ToolResult(
        success=success,
        result=json.dumps(payload),
        ui_summary=str(payload.get("note") or payload.get("status") or ""),
    )


class RefineTool(BaseTool):
    name = "refine"
    tool_icon = "◈"
    params = RefineParams
    # Bookkeeping on the session's harness store, never a source-file edit, so
    # it must not cost the user a permission prompt (same call as `goal`).
    mutating = False
    prompt_guidelines = (
        'refine(action="run", instructions="...") once you notice a repeated '
        "failure, a reusable tactic, or a behavior correction worth keeping; "
        'refine(action="status") to check whether a pass is already queued. '
        "It returns immediately and runs when the turn ends, so keep working",
    )
    description = (
        "Refine Vtx's continual harness: the persistent prompt notes, memories, "
        "skills, and subagent specs rendered into your system prompt. An "
        "auxiliary model reads the trajectory and applies small, evidence-backed "
        "create/update/delete edits, so lessons survive outside the context "
        "window. Runs at the end of the current turn, never mid-turn. Keep "
        "global_=false unless the lesson is clearly reusable across sessions."
    )

    def format_call(self, params: RefineParams) -> str:
        if params.action == "status":
            return "status"
        detail = params.instructions or ""
        if len(detail) > 50:
            detail = detail[:47] + "..."
        return f"run{' · global' if params.global_ else ''}" + (f" · {detail}" if detail else "")

    async def execute(
        self, params: RefineParams, cancel_event: asyncio.Event | None = None
    ) -> ToolResult:
        from vtx.ai.agent.rlm.registry import bridge_session_id, get_registry

        registry = get_registry(bridge_session_id())
        if params.action == "status":
            return _json(
                {
                    "status": "idle",
                    "pending": registry.refine_pending is not None,
                    "in_flight": registry.refine_in_flight,
                }
            )

        # Merge with any request already queued this turn: an omitted field
        # keeps the previous value instead of silently clearing it.
        previous = registry.refine_pending or {}
        registry.refine_pending = {
            "instructions": (
                params.instructions
                if params.instructions is not None
                else previous.get("instructions")
            ),
            "global": params.global_ or bool(previous.get("global")),
        }
        return _json({"scheduled": True, "note": REFINE_NOTE})


__all__ = ["MAX_INSTRUCTIONS_CHARS", "REFINE_NOTE", "RefineParams", "RefineTool"]
