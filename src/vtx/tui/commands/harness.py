"""/refine: run a continual-harness refinement pass on demand (prime parity)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vtx.tui.chat import ChatLog
from vtx.tui.commands.base import CommandSupport


def _refinement_status(outcome: Any) -> str:
    """One-line outcome for the status bar, matching the block's own wording.

    This used to always say "Refinement complete". A pass whose edits were all
    rejected (an edit missing its ``kind``, a baseline conflict, a malformed
    entry) renders a block titled "Harness refinement failed" and then
    immediately contradicted it in the status line — the two read as a
    contradiction because they are one, reported twice.
    """
    applied = getattr(outcome, "applied", 0)
    total = getattr(outcome, "total", 0)
    rollback = bool(getattr(outcome, "rollback_of", None))
    noun = "rollback" if rollback else "refinement"

    if total == 0:
        return f"Harness {noun} unchanged · no edits applied"
    if applied == 0:
        return f"Harness {noun} failed · no edits applied"
    if applied < total:
        return f"Harness {noun} partial · {applied}/{total} edits applied"
    return f"Harness {noun} complete · {applied} {'edit' if applied == 1 else 'edits'} applied"


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

    def _handle_harness_command(self, args: str) -> None:
        """``/harness [list|show|search|delete]`` — inspect and prune the store.

        Reads and deletes go straight to the store rather than through a
        refinement pass, so correcting a wrong entry costs nothing and does not
        require the model to agree. A delete reloads context, because the entry
        is also in the delivered digest the model sees.
        """
        from vtx.ai.agent.rlm.refine import parse_harness_command_options
        from vtx.ai.agent.rlm.registry import bridge_session_id

        chat = self.query_one("#chat-log", ChatLog)
        options = parse_harness_command_options(args)
        if options.errors:
            chat.add_info_message(options.errors[0], error=True)
            return

        session_id = bridge_session_id()
        cwd = getattr(self._runtime, "cwd", None) or "."

        if options.action == "list":
            self._harness_list(chat, session_id, cwd, options)
            return
        if options.action == "search":
            self._harness_search(chat, session_id, cwd, options)
            return
        if options.action == "show":
            self._harness_show(chat, session_id, cwd, options)
            return
        self._harness_delete(chat, session_id, cwd, options)

    def _harness_states(self, session_id: str, cwd: str, global_: bool):
        """The stores to read: both scopes, or just the requested one."""
        from vtx.ai.agent.rlm.harness import get_harness_state
        from vtx.ai.agent.rlm.refine import local_state_dir

        if global_:
            return [get_harness_state(global_=True)]
        return [
            get_harness_state(global_=True),
            get_harness_state(local_state_dir(session_id, cwd)),
        ]

    def _harness_list(self, chat: ChatLog, session_id: str, cwd: str, options: Any) -> None:
        entries = self._collect(session_id, cwd, options.global_, options.kind)
        scope = "global" if options.global_ else "local + global"
        title = f"Harness entries ({scope})"
        if options.kind:
            title += f" · {options.kind}"
        chat.add_harness_entries(entries, title=title, empty="  (none yet — run /refine)")

    def _collect(self, session_id: str, cwd: str, global_: bool, kind: str | None) -> list:
        """Entries from the requested scopes, global first, de-duplicated.

        A global id and a local id can be identical strings while being
        different entries, so the key includes the scope: collapsing them would
        hide one of the two from a user trying to find the one they saw.
        """
        from vtx.ai.agent.rlm.refine import KINDS

        kinds = [kind] if kind else list(KINDS)
        seen: set[tuple[str, str]] = set()
        entries = []
        for state in self._harness_states(session_id, cwd, global_):
            for current in kinds:
                for entry in state.list(current):
                    key = (entry.scope, entry.id)
                    if key in seen:
                        continue
                    seen.add(key)
                    entries.append(entry)
        return entries

    def _harness_search(self, chat: ChatLog, session_id: str, cwd: str, options: Any) -> None:
        query = options.target
        if not query:
            chat.add_info_message("Usage: /harness search <query>", error=True)
            return
        states = self._harness_states(session_id, cwd, options.global_)
        seen: set[tuple[str, str]] = set()
        hits = []
        for state in states:
            for entry in state.search(query, options.kind, limit=20):
                key = (entry.scope, entry.id)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(entry)
        if not hits:
            chat.add_info_message(f"No harness entries match {query!r}", warning=True)
            return
        chat.add_harness_entries(hits, title=f"Harness search · {query}", empty="  (no matches)")

    def _harness_show(self, chat: ChatLog, session_id: str, cwd: str, options: Any) -> None:
        from rich.text import Text

        from vtx.ai.agent.rlm.refine import resolve_harness_entry
        from vtx.ai.config import config

        found = resolve_harness_entry(session_id, cwd, options.target, global_=options.global_)
        if found is None:
            chat.add_info_message(
                f"No harness entry with id or title {options.target!r}", error=True
            )
            return
        entry, state = found
        colors = config.ui.colors
        text = Text()
        text.append(f"[{entry.kind} · {entry.id}]\n", style=colors.notice)
        text.append(f"{entry.title}\n", style=colors.fg)
        text.append(
            f"scope {entry.scope} · path {entry.path} · v{entry.version} · "
            f"created {entry.created_at} · updated {entry.updated_at}\n",
            style=colors.dim,
        )
        text.append("\n" + entry.content.strip() + "\n", style=colors.fg)
        for label, value in (
            ("reference", entry.reference),
            ("arguments", entry.arguments),
            ("metadata", entry.metadata),
        ):
            if value:
                import json

                text.append(
                    f"\n{label}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}\n"
                )
        text.append(f"\nstore: {state.file_path}\n", style=colors.dim)
        text.append(f"refinements recorded here: {len(state.refinements)}\n", style=colors.dim)
        chat.add_rich_message(text)

    def _harness_delete(self, chat: ChatLog, session_id: str, cwd: str, options: Any) -> None:
        from vtx.ai.agent.rlm.refine import resolve_harness_entry

        found = resolve_harness_entry(session_id, cwd, options.target, global_=options.global_)
        if found is None:
            chat.add_info_message(
                f"No harness entry with id or title {options.target!r}", error=True
            )
            return
        entry, state = found
        # `state` is the store that actually holds the entry, which is why the
        # lookup returns it rather than just the entry: a global entry found
        # through the merged view has to be removed from the global store or
        # the delete silently no-ops.
        try:
            removed = state.delete(entry.kind, entry.id)
        except Exception as e:
            chat.add_info_message(f"Could not delete {entry.id}: {e}", error=True)
            return
        if not removed:
            chat.add_info_message(
                f"{entry.kind} {entry.id} was already gone (changed on disk?)", warning=True
            )
            return
        # The deleted entry is in the delivered digest, so the model would keep
        # reading a notice about a memory that no longer exists.
        if self._runtime.session is not None:
            self._runtime.reload_context()
        chat.add_info_message(f"Deleted {entry.kind} [{entry.scope}:{entry.id}] {entry.title}")
        chat.show_status("Harness entry deleted")

    async def _do_refine(self, options: Any) -> None:
        from vtx.ai.agent.rlm.refine import run_refinement
        from vtx.ai.agent.rlm.registry import bridge_session_id

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
            from vtx.ai.agent.rlm.refine import append_refinement_notice

            append_refinement_notice(
                self._runtime.session,
                outcome.notice,
                source="user",
                refinement_id=outcome.id,
                summary=outcome.summary,
                scope=outcome.scope,
            )
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
        chat.show_status(_refinement_status(outcome))
