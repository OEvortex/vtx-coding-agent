"""Session lifecycle: /new, /clear, /tree, /session, /handoff,
/compact, /export, /copy, /undo, /redo, /usage and /recap."""

from __future__ import annotations

import asyncio
import builtins
import contextlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ctx.agent.session import Session, SessionInfo

from vx.commands.base import CommandSupport
from vx.tui import blocks, footer


def _clip_row(text: str, limit: int = 88) -> str:
    return text[: limit - 1] + "…" if len(text) > limit else text


#: How many tree nodes the picker offers; a long session is a deep list.
TREE_ROWS = 200


def session_title(path: Path) -> str:
    """The session's own name, from its row in the database."""
    from ctx.core import store

    try:
        row = store.session_meta(str(path))
    except Exception:
        return ""
    name = (row or {}).get("name")
    return name.strip() if isinstance(name, str) else ""


class SessionCommands(CommandSupport):
    # -- /clear and /new -------------------------------------------------

    def _reset_codemode_store(self) -> None:
        """Drop codemode's per-conversation store so stale values cannot leak."""
        for tool in getattr(self.runtime, "tools", []) or []:
            clear = getattr(tool, "clear_store", None)
            if callable(clear):
                with contextlib.suppress(Exception):
                    clear()

    def _handle_clear_command(self) -> None:
        rt = self.runtime
        if rt is not None and rt.session is not None:
            rt.new_session()
            self._reset_codemode_store()
        self.viewport.clear()
        self._reset_sidebar()
        self._welcome()
        self._ok("conversation cleared")

    def _handle_new_command(self) -> None:
        rt = self.runtime
        if rt is None:
            self._warn("the runtime is still starting")
            return
        rt.new_session()
        self._reset_codemode_store()
        with contextlib.suppress(Exception):
            rt.reload_context()
        self.viewport.clear()
        self._reset_sidebar()
        self._welcome()
        self._ok("started a new conversation")

    # -- /session picker -------------------------------------------------

    def _session_age(self, modified: datetime) -> str:
        now = datetime.now(UTC)
        seconds = max(0, int((now - modified).total_seconds()))
        minutes, hours, days = seconds // 60, seconds // 3600, seconds // 86400
        if minutes < 60:
            value, unit = minutes, "m"
        elif hours < 24:
            value, unit = hours, "h"
        elif days < 7:
            value, unit = days, "d"
        elif days < 365:
            value, unit = days // 7, "w"
        else:
            value, unit = days // 365, "y"
        return f"{value:>2}{unit}"

    def _session_items(self) -> list[footer.MenuItem]:
        sessions = Session.list(self.cwd)
        by_id: dict[str, SessionInfo] = {s.id: s for s in sessions}
        children: dict[str, list[SessionInfo]] = {}
        roots: list[SessionInfo] = []
        for session in sessions:
            parent = session.parent_session_id
            if parent and parent in by_id:
                children.setdefault(parent, []).append(session)
            else:
                roots.append(session)
        for kids in children.values():
            kids.sort(key=lambda s: s.modified, reverse=True)

        items: list[footer.MenuItem] = []
        current = self.runtime.session.id if self.runtime and self.runtime.session else ""

        def walk(node: SessionInfo, depth: int) -> None:
            indent = "   " * (depth - 1) + "└ [handoff] " if depth else ""
            dot = "● " if node.id == current else ""
            # A generated title reads better than the prompt that caused it.
            text = session_title(node.path) or node.first_message or "(empty session)"
            label = dot + " ".join(text.split())
            items.append(
                footer.MenuItem(
                    label=f"{indent}{label}",
                    detail=f"{node.message_count} messages",
                    metadata=self._session_age(node.modified),
                    value=node.id,
                )
            )
            for child in children.get(node.id, []):
                walk(child, depth + 1)

        for root in roots:
            walk(root, 0)
        return items

    def _show_session_switcher(self) -> None:
        """List the saved sessions and switch to the one you pick."""
        if not self._require_idle("switch sessions"):
            return
        items = self._session_items()
        if not items:
            self._warn("no saved sessions found")
            return
        self._open_menu(items, noun="Sessions", on_select=self._on_session_pick, filterable=True)

    def _on_session_pick(self, item: footer.MenuItem) -> None:
        session_id = item.value or ""
        rt = self.runtime
        if rt is not None and rt.session is not None and rt.session.id == session_id:
            self._ok("already in that session")
            return
        self._spawn(self._load_session_by_id(session_id))

    async def _load_session_by_id(self, session_id: str) -> None:
        rt = self.runtime
        if rt is None or not session_id:
            return
        try:
            session = Session.continue_by_id(self.cwd, session_id)
        except Exception as exc:
            self._fail(f"failed to load session: {exc}")
            return
        if session.session_file is None:
            self._fail("failed to load session: missing session file")
            return
        await self._load_session(session.session_file)

    async def _load_session(self, session_path: Path | str) -> None:
        rt = self.runtime
        if rt is None:
            return
        try:
            session = rt.load_session(session_path)
        except Exception as exc:
            self._fail(f"failed to load session: {exc}")
            return
        self.viewport.clear()
        self._welcome()
        self._render_session_history()
        self._refresh_stats(force=True)
        # A titled session is easier to recognise than its id.
        label = getattr(session, "name", None) or session.id[:8]
        self._ok(f"resumed {label}")

    # -- /tree -----------------------------------------------------------

    def _tree_items(self) -> list[footer.MenuItem]:
        rt = self.runtime
        if rt is None or rt.session is None:
            return []
        session = rt.session
        items: list[footer.MenuItem] = []

        def visit(node: Any, path: builtins.list[bool], last: bool) -> None:
            lead = "".join("   " if done else "│  " for done in path)
            if lead:
                lead += "└─ " if last else "├─ "
            label, kind = self._tree_row(node.entry)
            if label is not None:
                mark = " ✓" if node.entry.id == session.leaf_id else ""
                items.append(
                    footer.MenuItem(label=lead + label + mark, metadata=kind, value=node.entry.id)
                )
            children = node.children
            for index, child in enumerate(children):
                visit(child, [*path, last] if path else [], index == len(children) - 1)

        roots = session.get_tree()
        for index, root in enumerate(roots):
            visit(root, [], index == len(roots) - 1)
        return items[:TREE_ROWS]

    def _tree_row(self, entry: Any) -> tuple[str | None, str]:
        """One tree row: (label or None when hidden, kind for the metadata column)."""
        message = getattr(entry, "message", None)
        role = getattr(message, "role", None)
        if role is None:
            kind = type(entry).__name__.replace("Entry", "").lower()
            return None, kind
        content = getattr(message, "content", None)
        if isinstance(content, str):
            text = content
        else:
            text = "".join(
                str(getattr(part, "text", "") or "")
                for part in (content or [])
                if getattr(part, "text", None)
            )
        flat = " ".join(text.split())
        if role == "user":
            return _clip_row(f"user: {flat}"), "user"
        if role == "assistant":
            if not flat:
                calls = [
                    str(getattr(part, "name", "") or "")
                    for part in (content or [])
                    if getattr(part, "name", None)
                ]
                flat = "→ " + ", ".join(c for c in calls if c) if calls else "(no content)"
            return _clip_row(f"assistant: {flat}"), "assistant"
        if role == "tool":
            return _clip_row(f"[{getattr(message, 'tool_name', 'tool')}]"), "tool"
        return (None if not flat else _clip_row(flat)), str(role)

    def _handle_tree_command(self) -> None:
        if not self._require_idle("open the tree"):
            return
        rt = self.runtime
        if rt is None or rt.session is None or not rt.session.all_entries:
            self._warn("this session has no entries yet")
            return
        items = self._tree_items()
        if not items:
            self._warn("no entries in this session")
            return
        index = next(
            (i for i, item in enumerate(items) if item.value == rt.session.leaf_id), len(items) - 1
        )
        self._open_menu(
            items,
            noun="Conversation tree",
            on_select=self._on_tree_pick,
            selected=max(0, index),
            filterable=True,
        )

    def _on_tree_pick(self, item: footer.MenuItem) -> None:
        rt = self.runtime
        if rt is None or not item.value:
            return
        try:
            result = rt.navigate_tree(item.value)
        except Exception as exc:
            self._fail(f"tree navigation failed: {exc}")
            return
        self.viewport.clear()
        self._welcome()
        self._render_session_history()
        self._refresh_stats(force=True)
        if result.editor_text:
            self._input = result.editor_text
            self._cursor = len(self._input)
        self._ok("moved to that point in the conversation")

    # -- /session --------------------------------------------------------

    def _handle_session_command(self) -> None:
        self._show_session_switcher()

    # -- /usage ----------------------------------------------------------

    def _handle_usage_command(self) -> None:
        self._spawn(self._show_usage())

    async def _show_usage(self) -> None:
        """ctx's ``/usage``: session, rolling month and lifetime, over a year grid.

        Reading every session log takes seconds, so it runs in a worker thread.
        The wait is shown in the footer activity rather than the transcript: a
        pending row would have to be removed again, and vx's transcript is a
        record of what happened, not a scratch surface.
        """
        from ctx.term.usage_stats import HEATMAP_DAYS, DailyUsage, aggregate_usage

        self._activity = "reading usage…"
        try:
            report = await asyncio.to_thread(aggregate_usage)
        except Exception as exc:
            self._fail(f"could not read usage: {exc}", topic="usage")
            return
        finally:
            self._activity = ""

        session = DailyUsage()
        rt = self.runtime
        if rt is not None and rt.session is not None:
            totals = rt.session.token_totals()
            session = DailyUsage(
                input_tokens=totals.input_tokens,
                output_tokens=totals.output_tokens,
                cache_read_tokens=totals.cache_read_tokens,
                cache_write_tokens=totals.cache_write_tokens,
                turns=rt.session.message_counts().assistant_messages,
            )
        self.viewport.add(blocks.Usage(session=session, report=report, days=HEATMAP_DAYS))
        self.viewport.touch()

    # -- /compact --------------------------------------------------------

    def _handle_compact_command(self, args: str) -> None:
        rt = self.runtime
        if rt is None or rt.session is None or not rt.session.all_messages:
            self._warn("no conversation to compact")
            return
        if not self._require_idle("compact"):
            return
        self._notice("compacting the context window…", topic="compact")
        self._spawn(self._do_compact(args.strip()))

    async def _do_compact(self, instructions: str) -> None:
        rt = self.runtime
        if rt is None or rt.session is None:
            return
        self._activity = "compacting…"
        try:
            result = await rt.compact_now(instructions or None)
        except Exception as exc:
            self._fail(f"compaction failed: {exc}", topic="compact")
            return
        finally:
            self._activity = ""
        self._ok(
            f"compacted {result.tokens_before:,} → {result.tokens_after:,} tokens", topic="compact"
        )
        if result.summary:
            self._notice(result.summary, topic="summary")
        self._refresh_stats(force=True)

    # -- /export and /copy -----------------------------------------------

    def _handle_export_command(self) -> None:
        rt = self.runtime
        session = rt.session if rt else None
        if session is None:
            self._warn("no active session to export")
            return
        if not session.entries:
            self._warn("this session has no messages to export")
            return
        try:
            from ctx.term.export import export_session_html

            from vx.brand import VERSION

            path = export_session_html(
                cwd=self.cwd, session_id=session.id, output_dir=self.cwd, version=VERSION
            )
        except Exception as exc:
            self._fail(f"export failed: {exc}")
            return
        self._ok(f"session exported to {path.name}", topic="export")

    def _handle_copy_command(self) -> None:
        rt = self.runtime
        session = rt.session if rt else None
        text = session.get_last_assistant_text() if session is not None else None
        if not text:
            self._fail("no agent messages to copy yet")
            return
        try:
            from ctx.term.clipboard import copy_to_clipboard

            copy_to_clipboard(text)
        except Exception as exc:
            self._fail(f"could not copy: {exc}")
            return
        self._ok(f"copied the last answer ({len(text):,} chars) to the clipboard")

    # -- /undo and /redo -------------------------------------------------

    def _handle_undo_command(self) -> None:
        from ctx.agent import revert as rv

        rt = self.runtime
        if rt is None or rt.session is None:
            self._fail("no active session")
            return
        if not self._require_idle("undo"):
            return
        target = rv.previous_boundary(rt.session)
        if target is None:
            self._warn("nothing left to undo")
            return
        try:
            state = rv.stage(rt.session, target, cwd=self.cwd)
        except rv.RevertError as exc:
            self._fail(str(exc))
            return
        self.viewport.clear()
        self._welcome()
        self._render_session_history()
        self._render_revert_state(state)
        self._refresh_stats(force=True)

    def _handle_redo_command(self) -> None:
        from ctx.agent import revert as rv

        rt = self.runtime
        if rt is None or rt.session is None:
            self._fail("no active session")
            return
        if not self._require_idle("redo"):
            return
        if rv.current_state(rt.session) is None:
            self._warn("nothing to redo — no revert is in effect")
            return
        target = rv.next_boundary(rt.session)
        if target is None:
            changed = rv.unrevert(rt.session, cwd=self.cwd)
            detail = f", {changed} files restored" if changed else ""
            self._ok(f"redone — back at the newest turn{detail}")
            return
        try:
            state = rv.stage(rt.session, target, cwd=self.cwd)
        except rv.RevertError as exc:
            self._fail(str(exc))
            return
        self.viewport.clear()
        self._welcome()
        self._render_session_history()
        self._render_revert_state(state, direction="forward")
        self._refresh_stats(force=True)

    def _render_revert_state(self, state: Any, *, direction: str = "back") -> None:
        from ctx.agent import revert

        summary = revert.describe(state)
        for prefix in ("Rewound", "Reverted"):
            if summary.startswith(prefix):
                summary = ("Moved" if direction == "forward" else prefix) + summary[len(prefix) :]
                break
        self._notice(summary, tone="information")
        files = getattr(state, "files", []) or []
        if files:
            rows = []
            for diff in files[:8]:
                mark = {"added": "A", "deleted": "D"}.get(diff.status, "M")
                delta = (
                    f"+{diff.additions} -{diff.deletions}"
                    if (diff.additions or diff.deletions)
                    else ""
                )
                rows.append((diff.path, f"{mark} {delta}".strip()))
            self._report(
                "Files restored",
                [("changed", rows)],
                detail="/redo to step forward again"
                if len(files) <= 8
                else f"… {len(files) - 8} more; /redo to step forward",
            )
        else:
            self._notice("/redo to step forward again")

    # -- /handoff --------------------------------------------------------

    def _handle_handoff_command(self, args: str) -> None:
        rt = self.runtime
        query = args.strip()
        if rt is None:
            self._warn("the runtime is still starting")
            return
        if not self._require_idle("hand off"):
            return
        if not query:
            self._fail("usage: /handoff <query>. example: /handoff implement phase two")
            return
        if rt.session is None or not rt.session.all_messages:
            self._fail("no conversation to hand off")
            return
        self._notice("creating handoff…", topic="handoff")
        self._spawn(self._do_handoff(query))

    async def _do_handoff(self, query: str) -> None:
        rt = self.runtime
        if rt is None:
            return
        try:
            result = await rt.create_handoff(query)
        except Exception as exc:
            self._fail(f"handoff failed: {exc}", topic="handoff")
            return
        self.viewport.clear()
        self._welcome()
        self._render_session_history()
        self._input = result.prompt
        self._cursor = len(self._input)
        self._ok("handoff ready — press enter to send", topic="handoff")

    # -- /recap ----------------------------------------------------------

    def _handle_recap_command(self) -> None:
        rt = self.runtime
        if rt is None or rt.session is None or not rt.session.all_messages:
            self._warn("nothing to recap yet")
            return
        if not self._require_idle("draft a recap"):
            return
        self._spawn(self._do_recap())

    async def _do_recap(self) -> None:
        rt = self.runtime
        if rt is None:
            return
        # The box appears at once and fills when the words arrive, so a recap
        # of a long session never looks like a frozen command.
        await self._draft_recap(rt)


def _prompt_preview(text: str, limit: int = 120) -> str:
    """Shorten a prompt for a recap, naming the skill when one was triggered."""
    flat = " ".join(text.split())
    if flat.startswith("<skill"):
        end = flat.find(">")
        name = ""
        for chunk in flat[: end if end > 0 else 200].split():
            if chunk.startswith('name="'):
                name = chunk[6:].rstrip('">')
                break
        flat = f"/{name}" if name else "skill"
    return flat[: limit - 1] + "…" if len(flat) > limit else flat


def _local_recap(session: Session | None) -> str:
    """A recap without another model call: what was asked and what was done."""
    if session is None:
        return ""
    counts = session.message_counts()
    lines = [
        f"This session has {counts.user_messages} prompts, "
        f"{counts.assistant_messages} replies and {counts.tool_calls} tool calls."
    ]
    prompts: list[str] = []
    for message in session.messages:
        content = getattr(message, "content", None)
        if isinstance(content, str) and getattr(message, "role", "") == "user":
            prompts.append(_prompt_preview(content))
    for prompt in prompts[-4:]:
        lines.append(f"- {prompt}")
    return "\n\n".join(lines)
