"""VTX Claw runtime — extends VTX's ConversationRuntime with Claw-specific behavior."""

from __future__ import annotations

from typing import Any

from vtx.runtime import ConversationRuntime

from .prompts import build_system_prompt as claw_build_system_prompt


class ClawConversationRuntime(ConversationRuntime):
    """ConversationRuntime subclass that uses Claw's custom system prompt."""

    def _rebuild_system_prompt(self) -> None:
        """Re-render the system prompt using Claw's custom builder."""
        active = self.active_agent
        extra = active.definition.instructions if active is not None else None
        mode = active.definition.instructions_mode if active is not None else "append"
        # Filter skills to the ones explicitly listed by the active agent, if any.
        agent_skills: list[Any] | None = None
        if active is not None and active.definition.skills and self.context is not None:
            names = set(active.definition.skills)
            agent_skills = [s for s in self.context.skills if s.name in names or s.path in names]
        new_prompt = claw_build_system_prompt(
            self.cwd,
            context=self.context,
            tools=self.tools,
            extra_instructions=extra,
            extra_instructions_mode=mode,
            skills=agent_skills,
        )
        # Agent._system_prompt is intentionally a private attribute we
        # rebuild on activation; the type checker doesn't know that.
        if self.agent is not None:
            self.agent._system_prompt = new_prompt  # type: ignore[attr-defined]
