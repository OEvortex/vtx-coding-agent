"""Session lifecycle commands: /clear, /new, /resume, /tree, /session, /handoff,
/compact, /export and /copy."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from pathlib import Path

from vtx.agent.session import Session, SessionInfo
from vtx.core.config import config
from vtx.tui.chat import ChatLog
from vtx.tui.clipboard import copy_to_clipboard
from vtx.tui.commands.base import CommandSupport
from vtx.tui.floating_list import FloatingList, ListItem
from vtx.tui.input import InputBox
from vtx.tui.selection_mode import SelectionMode
from vtx.tui.tree import TreeSelector
from vtx.tui.widgets import InfoBar, StatusLine, format_path


class SessionCommands(CommandSupport):
    HANDOFF_BACKLINK_TYPE = "handoff_backlink"
    HANDOFF_FORWARD_LINK_TYPE = "handoff_forward_link"

    def _clear_conversation(self) -> None:
        if self._runtime.session:
            self._runtime.new_session()
            self._sync_runtime_state()
            self._reset_codemode_store()
            info_bar = self.query_one("#compact-footer", InfoBar)
            info_bar.set_tokens(0, 0, 0, 0)
            info_bar.set_file_changes({})
        chat = self.query_one("#chat-log", ChatLog)
        chat.add_info_message("Conversation cleared")

    def _reset_codemode_store(self) -> None:
        """Drop the codemode session store.

        The store is what carries `store(...)` writes between codemode calls,
        and it is scoped to a conversation. Carrying it across `/clear` would
        re-inject results from the previous conversation into a script that had
        no way to know they were stale.
        """
        for tool in self._runtime.tools:
            clear = getattr(tool, "clear_store", None)
            if callable(clear):
                clear()

    def _new_conversation(self) -> None:
        self._runtime.new_session()
        self._sync_runtime_state()
        self._reset_codemode_store()

        chat = self.query_one("#chat-log", ChatLog)
        info_bar = self.query_one("#compact-footer", InfoBar)
        status = self.query_one("#status-line", StatusLine)

        self.run_worker(self._do_new_conversation(chat, info_bar, status), exclusive=False)

    async def _do_new_conversation(self, chat: ChatLog, info_bar, status) -> None:
        await self._reset_session_ui(chat, info_bar, status)
        chat.add_info_message("Started new conversation")

    async def _reset_session_ui(self, chat: ChatLog, info_bar, status) -> None:
        await chat.remove_all_children()

        status.reset()

        info_bar.set_tokens(0, 0, 0, 0)
        info_bar.set_file_changes({})
        info_bar.set_thinking_level(self._runtime.thinking_level)

        chat.add_session_info(getattr(self, "VERSION", ""))

        self._runtime.reload_context()
        self._sync_runtime_state()
        if self._runtime.agent is not None:
            self._sync_slash_commands()
            # TODO: Surface self._runtime.agent.context.skill_warnings in UI
            chat.add_loaded_resources(
                context_paths=[
                    format_path(f.path) for f in self._runtime.agent.context.agents_files
                ],
                skills=self._runtime.agent.context.skills,
                tools=self._runtime.tools,
            )

    def _handle_handoff_command(self, args: str) -> None:
        chat = self.query_one("#chat-log", ChatLog)

        if self._is_running:
            chat.add_info_message("Cannot handoff while agent is running", error=True)
            return

        if (
            self._runtime.provider is None
            or self._runtime.session is None
            or self._runtime.agent is None
        ):
            chat.add_info_message("Agent not initialized", error=True)
            return

        query = args.strip()
        if not query:
            chat.add_info_message(
                "Usage: /handoff <query>. Example: /handoff implement phase two", error=True
            )
            return

        if not self._runtime.session.all_messages:
            chat.add_info_message("No conversation to handoff", error=True)
            return

        chat.show_spinner_status("Creating handoff...")
        self.run_worker(self._do_handoff(query), exclusive=False)

    def _resolve_system_prompt(self, session: Session | None = None) -> str:
        return self._runtime.resolve_system_prompt(session)

    def _create_new_session(self) -> Session:
        return self._runtime.create_session()

    async def _do_handoff(self, query: str) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        info_bar = self.query_one("#compact-footer", InfoBar)
        status = self.query_one("#status-line", StatusLine)
        input_box = self.query_one("#input-box", InputBox)

        if (
            self._runtime.provider is None
            or self._runtime.session is None
            or self._runtime.agent is None
        ):
            chat.add_info_message("Agent not initialized", error=True)
            return

        try:
            result = await self._runtime.create_handoff(query)
        except Exception as e:
            chat.show_status("Handoff failed")
            chat.add_info_message(f"Handoff failed: {e}", error=True)
            return

        self._sync_runtime_state()
        await self._reset_session_ui(chat, info_bar, status)
        self._render_session_entries(result.new_session)

        input_box.clear()
        input_box.insert(result.prompt)
        chat.show_status("Handoff ready")
        input_box.focus()

    def _show_session_info(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        if not self._runtime.session:
            chat.add_info_message("No active session")
            return

        session_path = self._runtime.session.session_file
        session_dir = str(session_path.parent) if session_path else None
        session_file = session_path.name if session_path else "(in-memory session)"

        counts = self._runtime.session.message_counts()
        token_totals = self._runtime.session.token_totals()

        chat.add_session_details(
            session_dir=session_dir,
            session_file=session_file,
            user_messages=counts.user_messages,
            assistant_messages=counts.assistant_messages,
            tool_calls=counts.tool_calls,
            tool_results=counts.tool_results,
            total_messages=counts.total_messages,
            input_tokens=token_totals.input_tokens,
            output_tokens=token_totals.output_tokens,
            cache_read_tokens=token_totals.cache_read_tokens,
            cache_write_tokens=token_totals.cache_write_tokens,
            total_tokens=token_totals.total_tokens,
        )

    def _build_resume_items(self) -> list[ListItem]:
        sessions = Session.list(self._cwd)

        # Build tree structure from handoff relationships
        by_id: dict[str, SessionInfo] = {s.id: s for s in sessions}
        children: dict[str, list[SessionInfo]] = {}
        roots: list[SessionInfo] = []

        for session in sessions:
            pid = session.parent_session_id
            if pid and pid in by_id:
                children.setdefault(pid, []).append(session)
            else:
                roots.append(session)

        # Sort children within each parent by modified time (newest first,
        # matching the root-level sort from Session.list)
        for kids in children.values():
            kids.sort(key=lambda s: s.modified, reverse=True)

        # DFS flatten: roots are already sorted by modified (from Session.list)
        items: list[ListItem] = []
        accent = config.ui.colors.accent

        def _walk(node: SessionInfo, depth: int) -> None:
            prefix = ""
            if depth > 0:
                prefix = f"{'   ' * (depth - 1)} └ [handoff] "
            label = self._format_session_label(node.first_message)
            caption = f"{self._format_session_age(node.modified)} {node.message_count}"
            items.append(
                ListItem(
                    value=node,
                    label=label,
                    description=caption,
                    prefix=prefix,
                    prefix_style=accent,
                )
            )
            for child in children.get(node.id, []):
                _walk(child, depth + 1)

        for root in roots:
            _walk(root, 0)

        return items

    # ------------------------------------------------------------------
    # /undo and /redo
    # ------------------------------------------------------------------

    def _revert_chat(self) -> ChatLog:
        return self.query_one("#chat-log", ChatLog)

    def _revert_session(self) -> Session | None:
        session = getattr(self._runtime, "session", None)
        return session if session is not None else None

    def _handle_undo_command(self) -> None:
        """Rewind one turn: restore the agent's files, stage the boundary.

        The boundary is not committed here, so ``/redo`` still works until the
        user sends their next prompt.
        """
        from vtx.agent import revert as rv

        chat = self._revert_chat()
        if self._is_running:
            chat.add_info_message("Cannot undo while the agent is working", error=True)
            return
        session = self._revert_session()
        if session is None:
            chat.add_info_message("No active session", error=True)
            return
        target = rv.previous_boundary(session)
        if target is None:
            chat.add_info_message("Nothing left to undo")
            return
        try:
            state = rv.stage(session, target, cwd=getattr(self, "_cwd", ""))
        except rv.RevertError as exc:
            chat.add_info_message(str(exc), error=True)
            return
        self._render_revert_state(chat, state, redo=True)

    def _handle_redo_command(self) -> None:
        """Step forward again, or clear the revert when already at the newest turn."""
        from vtx.agent import revert as rv

        chat = self._revert_chat()
        if self._is_running:
            chat.add_info_message("Cannot redo while the agent is working", error=True)
            return
        session = self._revert_session()
        if session is None:
            chat.add_info_message("No active session", error=True)
            return
        if rv.current_state(session) is None:
            chat.add_info_message("Nothing to redo — no revert is in effect")
            return
        target = rv.next_boundary(session)
        if target is None:
            changed = rv.unrevert(session, cwd=getattr(self, "_cwd", ""))
            detail = f", {changed} files restored" if changed else ""
            chat.add_info_message(f"Redone — back at the newest turn{detail}")
            return
        try:
            state = rv.stage(session, target, cwd=getattr(self, "_cwd", ""))
        except rv.RevertError as exc:
            chat.add_info_message(str(exc), error=True)
            return
        self._render_revert_state(chat, state, redo=True)

    def _render_revert_state(self, chat: ChatLog, state, *, redo: bool) -> None:
        """Post the in-chat revert marker with its file list and /redo hint."""
        from rich.text import Text

        from vtx.agent import revert

        colors = config.ui.colors
        text = Text()
        text.append("↩ ", style=f"bold {colors.accent}")
        text.append(revert.describe(state), style=colors.fg)
        if state.reverted_entries:
            text.append(f"  ({state.reverted_entries} entries rewound)", style=colors.dim)
        if not state.files_available:
            text.append(
                "\nfiles were left as-is: this turn predates snapshotting "
                "(resumed session). /redo still works.",
                style=colors.notice,
            )
        for diff in state.files[:8]:
            text.append("\n    ")
            mark = {"added": "A", "deleted": "D"}.get(diff.status, "M")
            style = {"added": colors.success, "deleted": colors.failed}.get(
                diff.status, colors.dim
            )
            text.append(f"{mark} ", style=style)
            text.append(diff.path, style=colors.muted)
            if diff.additions or diff.deletions:
                text.append(f"  +{diff.additions} -{diff.deletions}", style=colors.dim)
        if len(state.files) > 8:
            text.append(f"\n    … {len(state.files) - 8} more", style=colors.dim)
        if redo:
            text.append("\n/redo to step forward again", style=colors.dim)
        chat.add_rich_message(text)

    def _show_tree_selector(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        input_box = self.query_one("#input-box", InputBox)
        if self._is_running:
            chat.add_info_message("Cannot open tree while agent is running", error=True)
            return
        if not self._runtime.session or not self._runtime.session.all_entries:
            chat.add_info_message("No entries in session")
            return
        tree = self._runtime.session.get_tree()
        selector = self.query_one("#tree-selector", TreeSelector)
        input_box.clear()
        input_box.set_autocomplete_enabled(False)
        input_box.set_completing(True)
        size = getattr(self, "size", None)
        height = size.height if size is not None else 24
        selector.show(tree, self._runtime.session.leaf_id, height)
        self._selection_mode = SelectionMode.TREE

    def _show_resume_sessions(self) -> None:
        items = self._build_resume_items()
        if not items:
            self.notify(
                "No saved sessions found", title="Sessions", timeout=3, severity="information"
            )
            return

        self._show_selection_picker(items, SelectionMode.SESSION, max_label_width=87)

    def _delete_selected_resume_session(self) -> None:
        if self._selection_mode != SelectionMode.SESSION:
            return

        completion_list = self.query_one("#completion-list", FloatingList)
        selected_item = completion_list.selected_item
        if selected_item is None:
            return

        session_info = selected_item.value
        session_path = Path(session_info.path)

        current_session_path: Path | None = None
        if self._runtime.session and self._runtime.session.session_file is not None:
            current_session_path = Path(self._runtime.session.session_file)

        if current_session_path is not None and session_path == current_session_path:
            self.notify(
                "Cannot delete current session", title="Sessions", timeout=2, severity="warning"
            )
            return

        try:
            session_path.unlink()
        except FileNotFoundError:
            pass
        except Exception as exc:
            self.notify(
                f"Failed to delete session: {exc}", title="Sessions", timeout=3, severity="error"
            )
            return

        items = self._build_resume_items()
        was_at_bottom = self._is_chat_at_bottom()
        if not items:
            self._hide_completion_list()
            input_box = self.query_one("#input-box", InputBox)
            input_box.set_autocomplete_enabled(True)
            input_box.set_completing(False)
            self._selection_mode = None
            self.notify(
                "Session deleted (no saved sessions left)",
                title="Sessions",
                timeout=2,
                severity="information",
            )
        else:
            completion_list.update_items(items)
            self.notify("Session deleted", title="Sessions", timeout=2, severity="information")

        self._restore_chat_scroll_after_refresh(was_at_bottom)

    def _handle_export_command(self) -> None:
        from vtx.tui.export import export_session_html

        chat = self.query_one("#chat-log", ChatLog)

        if not self._runtime.session:
            chat.add_info_message("No active session to export")
            return

        if not self._runtime.session.entries:
            chat.add_info_message("Session has no messages to export")
            return

        try:
            path = export_session_html(
                cwd=self._cwd,
                session_id=self._runtime.session.id,
                output_dir=self._cwd,
                version=getattr(self, "VERSION", ""),
            )
            chat.add_info_message(f"Session exported to {path.name}")
        except Exception as e:
            chat.add_info_message(f"Export failed: {e}", error=True)

    def _handle_copy_command(self) -> None:
        chat = self.query_one("#chat-log", ChatLog)

        if not self._runtime.session:
            chat.add_info_message("No agent messages to copy yet", error=True)
            return

        text = self._runtime.session.get_last_assistant_text()
        if not text:
            chat.add_info_message("No agent messages to copy yet", error=True)
            return

        copy_to_clipboard(text)
        chat.show_status("Copied last agent message to clipboard")

    def _handle_compact_command(self, args: str = "") -> None:
        chat = self.query_one("#chat-log", ChatLog)

        if self._is_running:
            chat.add_info_message("Cannot compact while agent is running", error=True)
            return

        if self._runtime.provider is None or self._runtime.session is None:
            chat.add_info_message("Agent not initialized", error=True)
            return

        if not self._runtime.session.all_messages:
            chat.add_info_message("No conversation to compact", error=True)
            return

        instructions = (args or "").strip()
        tokens_before = self._runtime.latest_assistant_usage_tokens()
        if not tokens_before and self._runtime.session is not None:
            with contextlib.suppress(Exception):
                tokens_before = int(self._runtime.session.token_totals().context_tokens)
        chat.start_compaction(tokens_before=tokens_before, trigger="manual")
        self.run_worker(self._do_compact(instructions), exclusive=False)

    async def _do_compact(self, instructions: str = "") -> None:
        chat = self.query_one("#chat-log", ChatLog)

        if self._runtime.provider is None or self._runtime.session is None:
            chat.add_info_message("Agent not initialized", error=True)
            return

        try:
            result = await self._runtime.compact_now(
                instructions or None,
                on_progress=lambda p: chat.update_compaction_progress(p.chars, p.sections_started),
            )
            chat.finish_compaction(
                tokens_before=result.tokens_before,
                tokens_after=result.tokens_after,
                summary=result.summary,
            )
            # Same reason as the auto-compaction path in agent_runner: the bar
            # still holds the pre-compaction footprint, and nothing would
            # correct it until the user sent another message.
            self.query_one("#compact-footer", InfoBar).sync_tokens_from_session(
                self._runtime.session
            )
        except Exception as e:
            chat.finish_compaction(tokens_before=0, tokens_after=0, error=str(e))

    def _format_session_label(self, message: str) -> str:
        return " ".join(message.split())

    def _format_session_age(self, modified: datetime) -> str:
        now = datetime.now(UTC)
        delta = max(0, int((now - modified).total_seconds()))
        minutes = delta // 60
        hours = delta // 3600
        days = delta // 86400
        weeks = days // 7

        if minutes < 60:
            value, unit = minutes, "m"
        elif hours < 24:
            value, unit = hours, "h"
        elif days < 7:
            value, unit = days, "d"
        elif weeks < 52:
            value, unit = weeks, "w"
        else:
            value, unit = weeks // 52, "y"

        return f"{value:>2}{unit}"
