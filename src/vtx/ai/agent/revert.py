"""Turn-level undo/redo over the session tree and the working directory.

Model
-----

Undo and redo are **not a stack**. Both move a single boundary — the session
leaf — along a linear list of user messages, and both restore files from a
snapshot tree. There is no redo stack to truncate and the two directions can
never desync, because the structure *is* the history.

Rewinding is **non-destructive**: committing a revert appends a
:class:`~vtx.ai.agent.session.LeafEntry` pointing at the boundary, so the
discarded branch stays in the entry tree. That is what makes redo free, and
it is strictly safer than deleting entries, which cannot be undone.

Phases
------

1. ``stage``   — diff the files the agent touched since the boundary, restore
                 them, and record revert state. Messages are untouched.
2. preview    — the chat log shows "N messages reverted" plus the file list,
                 and ``/redo`` still works.
3. ``commit``  — the boundary is applied, dropping the reverted turns from the
                 active branch. Runs when the user next sends a prompt, so a
                 revert is never final until they actually continue.

Safety
------

Only files recorded in a tool result's ``file_changes`` between the boundary
and the tip are restored. A user editing the same worktree by hand is common,
and a revert that silently clobbers their work is worse than no revert at all.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from vtx.ai.agent.snapshot import FileDiff, SnapshotStore, get_store
from vtx.core.types import ToolResultMessage, UserMessage

if TYPE_CHECKING:
    from vtx.ai.agent.session import Session

log = logging.getLogger("agent.revert")

#: Hidden entry recording the worktree tree id as of a turn's start.
SNAPSHOT_ENTRY = "vtx-revert-snapshot"
#: Hidden entry holding the staged revert state.
REVERT_ENTRY = "vtx-revert"


class RevertError(RuntimeError):
    """A revert was requested that cannot be satisfied."""


@dataclass
class RevertState:
    """The staged revert currently in effect."""

    boundary_id: str
    boundary_label: str
    tree: str
    original_tree: str
    files: list[FileDiff] = field(default_factory=list)
    reverted_entries: int = 0
    agent_files: int = 0

    @property
    def additions(self) -> int:
        return sum(d.additions for d in self.files)

    @property
    def deletions(self) -> int:
        return sum(d.deletions for d in self.files)

    @property
    def files_available(self) -> bool:
        """False when the boundary predates snapshotting, so no restore ran.

        An empty ``tree`` is the marker: there was no recorded worktree state
        for that point, only the conversation rewind.
        """
        return bool(self.tree)

    def to_json(self) -> str:
        return json.dumps(
            {
                "boundaryId": self.boundary_id,
                "boundaryLabel": self.boundary_label,
                "tree": self.tree,
                "originalTree": self.original_tree,
                "files": [
                    {
                        "path": d.path,
                        "status": d.status,
                        "additions": d.additions,
                        "deletions": d.deletions,
                    }
                    for d in self.files
                ],
                "revertedEntries": self.reverted_entries,
            }
        )

    @classmethod
    def from_json(cls, blob: str) -> RevertState | None:
        try:
            data = json.loads(blob)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict) or not data.get("boundaryId"):
            return None
        files = [
            FileDiff(
                path=str(item.get("path", "")),
                status=str(item.get("status", "modified")),
                additions=int(item.get("additions", 0) or 0),
                deletions=int(item.get("deletions", 0) or 0),
            )
            for item in data.get("files", [])
            if isinstance(item, dict) and item.get("path")
        ]
        return cls(
            boundary_id=str(data["boundaryId"]),
            boundary_label=str(data.get("boundaryLabel", "")),
            tree=str(data.get("tree", "")),
            original_tree=str(data.get("originalTree", "")),
            files=files,
            reverted_entries=int(data.get("revertedEntries", 0) or 0),
        )


# ---------------------------------------------------------------------------
# snapshot bookkeeping
# ---------------------------------------------------------------------------


def record_turn_snapshot(session: Session, tree: str | None) -> str | None:
    """Record the worktree state at the start of a turn.

    Must be called *before* the agent can touch files, so the recorded tree is
    a valid revert target for that turn.
    """
    if not tree:
        return None
    try:
        return session.append_custom_message(
            SNAPSHOT_ENTRY, tree, display=False, details={"tree": tree}
        )
    except Exception:
        log.exception("failed to record turn snapshot")
        return None


def capture_now(cwd: str) -> str | None:
    """Hash the worktree right now, if snapshots are available here."""
    try:
        return get_store(cwd).capture()
    except Exception:
        log.exception("snapshot capture failed")
        return None


def _tree_at(session: Session, boundary_id: str) -> str | None:
    """Tree id recorded for the turn that ``boundary_id`` starts.

    A turn's snapshot is adjacent to its user message, and the runtime always
    writes it *first*: the worktree is hashed at turn start and ``loop.py``
    appends the user message from inside ``agent.run``. A session rewound by
    ``commit`` lands the same way, because the next turn's snapshot is
    appended at the restored leaf. So the snapshot for a turn is the one
    immediately preceding its user message, and the search runs backwards.

    The backward bound matters: a turn with no snapshot of its own (a session
    resumed from disk, say) would otherwise reach back into the *previous*
    turn and restore files to a state that still contains the changes being
    reverted. Searching forwards would be worse still — in this ordering the
    next turn's snapshot sits between this turn's results and the next user
    message, so a forward scan would happily claim it.
    """
    branch = session.get_branch()
    index = next((i for i, e in enumerate(branch) if e.id == boundary_id), None)
    if index is None:
        return None
    for entry in reversed(branch[:index]):
        if entry.type == "message" and isinstance(entry.message, UserMessage):
            return None
        if entry.type == "custom_message" and entry.custom_type == SNAPSHOT_ENTRY:
            return entry.content or None
    return None


def _agent_touched_files(session: Session, boundary_id: str) -> set[str]:
    """Paths the agent reported changing at or after the boundary.

    This is the guard that keeps a revert from destroying edits the user made
    by hand in the same worktree.

    It walks *descendants* of the boundary across every branch rather than the
    active branch alone. A committed revert moves the turns it discards off
    the active branch, and those turns are exactly the ones whose edits we are
    undoing — reading only the live branch would find no claimed files and
    silently restore nothing.
    """
    children: dict[str, list[Any]] = {}
    for entry in session.all_entries:
        if entry.parent_id:
            children.setdefault(entry.parent_id, []).append(entry)

    touched: set[str] = set()
    seen: set[str] = set()
    queue = [boundary_id]
    while queue:
        current = queue.pop()
        for entry in children.get(current, []):
            if entry.id in seen:
                continue
            seen.add(entry.id)
            if entry.type == "message" and isinstance(entry.message, ToolResultMessage):
                changes = entry.message.file_changes
                if changes and changes.path:
                    touched.add(changes.path)
            queue.append(entry.id)
    return touched


# ---------------------------------------------------------------------------
# staged state
# ---------------------------------------------------------------------------


def _staged_entry(session: Session) -> Any | None:
    """The live staged-revert entry on the active branch, if any."""
    for entry in reversed(session.get_branch()):
        if entry.type == "custom_message" and entry.custom_type == REVERT_ENTRY:
            return entry
    return None


def _drop_staged_state(session: Session) -> None:
    """Branch away from the staged revert so it is no longer live.

    Consecutive ``stage`` calls must not leave a trail of revert entries on
    the branch: only the newest one counts, and an older one left behind would
    resurface as "a revert is in effect" after the newest was cleared.
    """
    entry = _staged_entry(session)
    if entry is None:
        return
    parent = entry.parent_id
    if parent is None:
        return
    try:
        session.move_to(parent)
    except Exception:
        log.exception("failed to drop staged revert state")


def current_state(session: Session) -> RevertState | None:
    """The staged revert, if one is in effect."""
    entry = _staged_entry(session)
    return RevertState.from_json(entry.content) if entry is not None else None


def clear_state(session: Session) -> None:
    """Drop the staged revert by branching away from its entry."""
    _drop_staged_state(session)


def user_messages(session: Session) -> list[Any]:
    """User message entries on the active branch, oldest first."""
    out = []
    for entry in session.get_branch():
        if entry.type == "message" and isinstance(entry.message, UserMessage):
            out.append(entry)
    return out


def _label(entry: Any) -> str:
    message = getattr(entry, "message", None)
    text = ""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            getattr(part, "text", "") for part in content if getattr(part, "type", "") == "text"
        )
    text = " ".join(text.split())
    return text[:70] + "…" if len(text) > 70 else text


# ---------------------------------------------------------------------------
# stage / unrevert / commit
# ---------------------------------------------------------------------------


def stage(
    session: Session,
    boundary_id: str,
    *,
    store: SnapshotStore | None = None,
    cwd: str | None = None,
) -> RevertState:
    """Stage a revert to ``boundary_id``: restore files, keep messages.

    The reverted turns stay on the active branch so ``/redo`` can walk forward
    again. :func:`commit` applies the boundary.

    A boundary older than the earliest snapshot (typically a session resumed
    from disk, or one recorded before snapshots existed) still rewinds the
    conversation, but file restore is skipped and the returned state says so.
    Refusing outright would be worse: the conversation rewind is lossless and
    often all the user wanted.
    """
    if cwd is None:
        raise RevertError("cwd is required to stage a revert")
    store = store or get_store(cwd)

    # Read the pre-revert tree before dropping the previous staged state, so
    # repeated undos still point `original_tree` at the true starting worktree.
    existing = current_state(session)
    original_tree = (existing.original_tree if existing else "") or ""
    _drop_staged_state(session)

    boundary_tree = _tree_at(session, boundary_id)
    if not boundary_tree:
        # Older than anything we snapshotted. Rewind the conversation only:
        # there is no recorded tree for that point, so claiming to restore
        # files would be a lie.
        return _stage_conversation_only(session, boundary_id, original_tree)

    current_tree = store.capture()
    if not current_tree:
        raise RevertError("could not snapshot the current worktree")

    # Only the agent's own files. Anything the user changed by hand since the
    # boundary is left alone even if it differs between the two trees.
    # Tool results report absolute paths; git tree entries are project
    # relative, so both sides are normalized before they are compared.
    claimed = _agent_touched_files(session, boundary_id)
    agent_files = {rel for rel in (store.relative(p) for p in claimed) if rel}
    if not store.available():
        raise RevertError("snapshots need a git repository in this project")

    changed = store.files(boundary_tree, current_tree)
    targets = [p for p in changed if p in agent_files]

    if targets:
        store.restore({path: boundary_tree for path in targets})

    diffs = store.diff(boundary_tree, store.capture() or current_tree, targets)
    after_tree = store.capture() or current_tree

    branch = session.get_branch()
    index = next((i for i, e in enumerate(branch) if e.id == boundary_id), None)
    reverted_entries = max(0, len(branch) - (index or 0) - 1)

    # Reuse the pre-revert tree across successive reverts so unrevert always
    # returns to the true original state instead of walking forward.
    original_tree = original_tree or current_tree

    state = RevertState(
        boundary_id=boundary_id,
        boundary_label=_label(branch[index]) if index is not None else "",
        tree=after_tree,
        original_tree=original_tree,
        files=diffs,
        reverted_entries=reverted_entries,
        agent_files=len(targets),
    )
    session.append_custom_message(REVERT_ENTRY, state.to_json(), display=False)
    return state


def _stage_conversation_only(
    session: Session, boundary_id: str, original_tree: str
) -> RevertState:
    """Rewind the conversation for a boundary that has no snapshot.

    Used for turns recorded before snapshots existed — typically a session
    resumed from disk. The branch rewind is lossless; only the file restore is
    unavailable, and the state records that so the UI can say so plainly.
    """
    branch = session.get_branch()
    index = next((i for i, e in enumerate(branch) if e.id == boundary_id), None)
    if index is None:
        raise RevertError(f"no such entry on this branch: {boundary_id!r}")
    state = RevertState(
        boundary_id=boundary_id,
        boundary_label=_label(branch[index]),
        tree="",
        original_tree=original_tree,
        files=[],
        reverted_entries=max(0, len(branch) - index - 1),
        agent_files=0,
    )
    session.append_custom_message(REVERT_ENTRY, state.to_json(), display=False)
    return state


def unrevert(
    session: Session, *, store: SnapshotStore | None = None, cwd: str | None = None
) -> int:
    """Undo a staged revert, restoring the worktree it changed.

    Returns the number of files put back.
    """
    state = current_state(session)
    if state is None:
        return 0
    store = store or (get_store(cwd) if cwd else None)
    if store is None or not state.original_tree or not state.files:
        clear_state(session)
        return 0
    paths = [d.path for d in state.files]
    if not paths:
        clear_state(session)
        return 0
    # The pre-revert content of each file is the *current* state minus what the
    # revert changed, so restore from the recorded original tree.
    report = store.restore({path: state.original_tree for path in paths})
    changed = len(report.restored) + len(report.deleted)
    clear_state(session)
    return changed


def commit(session: Session) -> str | None:
    """Apply the staged boundary, dropping the reverted turns.

    Non-destructive: appends a leaf entry rather than deleting, so the branch
    remains reachable and a later ``/redo`` can return to it.
    """
    state = current_state(session)
    if state is None:
        return None
    try:
        session.move_to(state.boundary_id)
    except ValueError:
        log.warning("revert boundary %s is no longer reachable", state.boundary_id)
        clear_state(session)
        return None
    return state.boundary_id


# ---------------------------------------------------------------------------
# navigation
# ---------------------------------------------------------------------------


def previous_boundary(session: Session) -> str | None:
    """Entry id to undo to: the user message before the current position.

    With a staged revert in place, the boundary is that revert's own boundary,
    so consecutive undos walk further back through history.
    """
    state = current_state(session)
    messages = user_messages(session)
    if not messages:
        return None
    if state is not None:
        ids = [e.id for e in messages]
        if state.boundary_id in ids:
            at = ids.index(state.boundary_id)
            if at == 0:
                return None
            return ids[at - 1]
        return None
    # No revert staged: undo to the last user message, which drops its turn.
    return messages[-1].id


def next_boundary(session: Session) -> str | None:
    """Entry id to redo to, or ``None`` when already at the newest turn.

    ``None`` with a staged revert means "nothing left to redo" and the caller
    should clear the revert instead.
    """
    messages = user_messages(session)
    if not messages:
        return None
    ids = [e.id for e in messages]
    state = current_state(session)
    if state is None:
        return None
    if state.boundary_id not in ids:
        return None
    at = ids.index(state.boundary_id)
    return ids[at + 1] if at + 1 < len(ids) else None


def describe(state: RevertState) -> str:
    """One-line summary for a chat info message."""
    label = state.boundary_label or "this point"
    if not state.files_available:
        return f'Rewound to "{label}" (conversation only — no file snapshot for that point)'
    if not state.files:
        return f'Reverted to "{label}" (no file changes)'
    added = sum(1 for d in state.files if d.status == "added")
    removed = sum(1 for d in state.files if d.status == "deleted")
    parts = [f"{len(state.files)} files"]
    if added:
        parts.append(f"{added} added")
    if removed:
        parts.append(f"{removed} removed")
    return f'Reverted to "{label}" ({", ".join(parts)})'
