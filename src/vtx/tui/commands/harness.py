"""/refine: run a continual-harness refinement pass on demand (prime parity)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vtx.tui.chat import ChatLog
from vtx.tui.commands.base import CommandSupport

if TYPE_CHECKING:
    pass


class HarnessCommands(CommandSupport):
    # App-surface attributes provided by the other mixins / Vtx main class.
    _runtime: Any
    _is_running: bool

    def _handle_refine_command(self, args: str) -> None:
        from vtx.ai.agent.rlm.refine import parse_refine_command_options
        from vtx.ai.agent.rlm.registry import bridge_session_id, get_registry

        chat = self.query_one("#chat-log", ChatLog)
        if self._runtime.provider is None or self._runtime.session is None:
            chat.add_info_message("Agent not initialized", error=True)
            return

        options = parse_refine_command_options(args)
        if options.errors:
            # Prime throws on the first usage error.
            chat.add_info_message(options.errors[0], error=True)
            return

        if self._is_running:
            # Queue for the loop's turn-boundary drain instead of racing the
            # in-flight turn (prime's refine.run schedules the same way).
            get_registry(bridge_session_id()).refine_pending = {
                "instructions": options.instructions,
                "global": options.global_,
                "rollbackId": options.rollback_id,
            }
            chat.add_info_message("Refinement scheduled; it runs at the next turn boundary")
            return

        chat.show_spinner_status("Refining continual harness...")
        self.run_worker(self._do_refine(options), exclusive=False)

    async def _do_refine(self, options: Any) -> None:
        from vtx.ai.agent.rlm.refine import run_refinement
        from vtx.ai.agent.rlm.registry import bridge_session_id
        from vtx.core.types import UserMessage

        chat = self.query_one("#chat-log", ChatLog)
        if self._runtime.provider is None or self._runtime.session is None:
            chat.add_info_message("Agent not initialized", error=True)
            return

        try:
            outcome = await run_refinement(
                messages=self._runtime.session.all_messages,
                provider=self._runtime.provider,
                session_id=bridge_session_id(),
                cwd=self._runtime.cwd,
                instructions=options.instructions,
                global_=options.global_,
                source="user",
                rollback_id=options.rollback_id,
            )
        except Exception as e:
            chat.show_status("Refinement failed")
            chat.add_info_message(f"Refinement failed: {e}", error=True)
            return

        # The notice is a conversation message (the model sees it next turn)
        # and the delivered digest changed, so refresh context.
        if outcome.notice:
            self._runtime.session.append_message(UserMessage(content=outcome.notice))
        self._runtime.reload_context()

        # The block carries the per-edit diffs; the notice is what the model
        # reads, so it is not also dumped into the transcript as an info line.
        chat.add_refinement(
            summary=outcome.summary,
            applied=outcome.applied,
            total=outcome.total,
            edits=outcome.edits,
            scope=outcome.scope,
            rollback_of=outcome.rollback_of,
            refinement_id=outcome.id,
            model=outcome.model,
        )
        chat.show_status("Refinement complete")
