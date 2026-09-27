"""Content-addressed working-tree snapshots, stored outside the project.

Snapshots live in a *shadow* git repository under the vtx config dir, seeded
from nothing and fed from the project worktree. Nothing is ever written to the
project's own git history: no commits, no branches, no ``refs/stash`` entries.
That matters because this runs automatically on every turn of an autonomous
session, and a user who inspects ``git log`` or ``git stash list`` afterwards
should find it untouched.

Design notes:

- A snapshot is a git *tree* id, so it is content-addressed: capturing the same
  worktree twice yields the same id and costs no extra storage.
- Restore is a per-path blob write, never ``git checkout``/``apply``. A checkout
  needs a coherent index and can conflict; a blob write is exact. A path that
  is absent from the target tree is deleted, matching "revert to the state
  that tree describes".
- ``.gitignore`` handling is free: git reads ``.gitignore`` files inside the
  worktree natively, so ``node_modules``/``.venv`` never get hashed.
- The project's own ``.git`` directory is skipped automatically by git's
  worktree-boundary rule.
- Oversized blobs are dropped from the index rather than hashed, so one stray
  2 GB artifact cannot stall every subsequent turn.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from vtx.core.paths import get_config_dir

log = logging.getLogger("agent.snapshot")

#: Blobs larger than this are excluded from a capture. Build artifacts and
#: stray logs are normally gitignored, but an unignored one must not be able
#: to make every turn pay a multi-second hashing cost.
MAX_BLOB_BYTES = 2 * 1024 * 1024

#: Per-invocation ceiling. `git add -A` on a large tree is the slow part.
GIT_TIMEOUT = 20

STATUS_ADDED = "added"
STATUS_MODIFIED = "modified"
STATUS_DELETED = "deleted"


@dataclass(frozen=True)
class FileDiff:
    """One file's change between two snapshots."""

    path: str
    status: str
    additions: int = 0
    deletions: int = 0
    patch: str = ""

    @property
    def display(self) -> str:
        if self.status == STATUS_ADDED:
            return f"+{self.additions}"
        if self.status == STATUS_DELETED:
            return f"-{self.deletions}"
        return f"+{self.additions} -{self.deletions}"


@dataclass
class RestoreReport:
    """What a restore actually did, for surfacing to the user."""

    restored: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class SnapshotError(RuntimeError):
    """Snapshot capture/restore failed in a way the caller should surface."""


def _run(
    args: list[str],
    *,
    git_dir: str | None = None,
    work_tree: str | None = None,
    binary: bool = False,
    cwd: str | None = None,
    stdin: str | None = None,
    timeout: int = GIT_TIMEOUT,
) -> tuple[int, bytes | str]:
    """Run git, returning ``(returncode, stdout)``.

    Never raises: git's failure modes here (no repo, missing object, path
    outside tree) are all expected and handled by the caller.
    """
    env = dict(os.environ)
    if git_dir:
        env["GIT_DIR"] = git_dir
    if work_tree:
        env["GIT_WORK_TREE"] = work_tree
    # Keep the shadow repo completely independent of the user's identity,
    # hooks, templates, signing, and gc config.
    env.update(
        {
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_ATTR_NOSYSTEM": "1",
        }
    )
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            input=stdin,
            timeout=timeout,
            text=not binary,
        )
    except FileNotFoundError:
        return 127, b"" if binary else ""
    except subprocess.TimeoutExpired:
        log.warning("git %s timed out", " ".join(args[:3]))
        return 124, b"" if binary else ""
    except Exception:
        log.exception("git %s failed", " ".join(args[:3]))
        return 1, b"" if binary else ""
    return result.returncode, result.stdout


def _text(value: bytes | str) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def _count(patch: str, marker: str) -> int:
    """Count added/removed lines in a unified diff, ignoring the file headers."""
    total = 0
    for line in patch.splitlines():
        if not line.startswith(marker):
            continue
        if line.startswith(("+++", "---")):
            continue
        total += 1
    return total


def _path_from_diff_header(header: str) -> str:
    """Pull the b-side path out of a ``diff --git a/x b/x`` header line.

    Paths containing spaces make the naive ``split()`` ambiguous, so the b-side
    is taken as everything after the final ``" b/"`` and unquoted.
    """
    marker = " b/"
    index = header.rfind(marker)
    if index < 0:
        return ""
    path = header[index + len(marker) :].strip()
    if path.startswith('"') and path.endswith('"') and len(path) > 1:
        path = path[1:-1]
    return path


class SnapshotStore:
    """Per-project snapshot store backed by one shadow git repository."""

    def __init__(self, cwd: str) -> None:
        self.cwd = str(Path(cwd).resolve())
        self._git_dir: str | None = None
        self._available: bool | None = None

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------

    def _root(self) -> Path:
        digest = hashlib.sha256(self.cwd.encode("utf-8")).hexdigest()[:16]
        return Path(get_config_dir()) / "snapshots" / digest

    @property
    def git_dir(self) -> str:
        """Path to the shadow repo's `.git`, creating it on first use."""
        if self._git_dir is None:
            root = self._root()
            git_dir = root / ".git"
            if not (git_dir / "HEAD").exists():
                root.mkdir(parents=True, exist_ok=True)
                code, _ = _run(["init", "--quiet", str(root)], cwd=str(root))
                if code != 0:
                    raise SnapshotError(f"could not create snapshot store at {root}")
            self._git_dir = str(git_dir)
        return self._git_dir

    def available(self) -> bool:
        """Whether snapshots can be taken here at all."""
        if self._available is not None:
            return self._available
        code, out = _run(["rev-parse", "--is-inside-work-tree"], cwd=self.cwd)
        self._available = code == 0 and _text(out).strip() == "true"
        if not self._available:
            log.debug("snapshots unavailable: %s is not a git work tree", self.cwd)
        return self._available

    def is_project_repo(self) -> bool:
        """True when the project has its own git history (distinct from the shadow)."""
        code, out = _run(["rev-parse", "--absolute-git-dir"], cwd=self.cwd)
        if code != 0:
            return False
        try:
            return Path(_text(out).strip()).resolve() != Path(self.git_dir).resolve()
        except OSError:
            return False

    # ------------------------------------------------------------------
    # capture
    # ------------------------------------------------------------------

    def capture(self) -> str | None:
        """Hash the current worktree into a tree id.

        Returns ``None`` when snapshots are unavailable or the capture failed;
        a failed capture must never break the turn that triggered it.
        """
        if not self.available():
            return None
        try:
            return self._capture()
        except SnapshotError:
            raise
        except Exception:
            log.exception("snapshot capture failed")
            return None

    def _capture(self) -> str | None:
        git_dir = self.git_dir
        code, out = _run(["add", "-A", "--", "."], git_dir=git_dir, work_tree=self.cwd)
        if code != 0:
            log.warning("snapshot add failed with code %s", code)
            return None
        self._drop_oversized_blobs(git_dir)
        code, out = _run(["write-tree"], git_dir=git_dir)
        if code != 0:
            return None
        tree = _text(out).strip()
        return tree or None

    def _drop_oversized_blobs(self, git_dir: str) -> None:
        """Unstage blobs over :data:`MAX_BLOB_BYTES` so they are never hashed.

        One ``cat-file --batch-check`` pass covers the whole index, so this
        costs a single extra git process rather than a walk of the worktree.
        """
        code, out = _run(["ls-files", "-s", "-z"], git_dir=git_dir)
        if code != 0:
            return
        entries = [item for item in _text(out).split("\0") if item.strip()]
        if not entries:
            return
        paths_by_oid: dict[str, list[str]] = {}
        for item in entries:
            meta, _, path = item.partition("\t")
            parts = meta.split()
            if len(parts) >= 3 and parts[1] == "blob":
                paths_by_oid.setdefault(parts[2], []).append(path)
        if not paths_by_oid:
            return
        code, out = _run(
            ["cat-file", "--batch-check"],
            git_dir=git_dir,
            stdin="".join(f"{oid}\n" for oid in paths_by_oid),
        )
        if code != 0:
            return
        for line in _text(out).splitlines():
            fields = line.split()
            if len(fields) < 3 or fields[1] != "blob":
                continue
            try:
                size = int(fields[2])
            except ValueError:
                continue
            if size > MAX_BLOB_BYTES:
                for path in paths_by_oid.get(fields[0], []):
                    self._unstage(git_dir, path)

    def _unstage(self, git_dir: str, path: str) -> None:
        code, _ = _run(
            ["rm", "--cached", "--quiet", "--", path], git_dir=git_dir, work_tree=self.cwd
        )
        if code != 0:
            _run(["update-index", "--force-remove", "--", path], git_dir=git_dir)
        log.debug("excluded oversized blob from snapshot: %s", path)

    # ------------------------------------------------------------------
    # compare
    # ------------------------------------------------------------------

    def files(self, from_tree: str, to_tree: str) -> list[str]:
        """Project-relative paths that differ between two trees."""
        code, out = _run(["diff", "--name-only", "-z", from_tree, to_tree], git_dir=self.git_dir)
        if code != 0:
            return []
        return [item for item in _text(out).split("\0") if item]

    def diff(
        self, from_tree: str, to_tree: str, paths: list[str] | None = None, *, context: int = 3
    ) -> list[FileDiff]:
        """Per-file diffs between two trees.

        Statuses and patch text come from two separate git invocations rather
        than one merged ``--raw --patch -z`` stream: the combined form
        interleaves NUL-separated header records with free-form patch text,
        which needs a real tokenizer. Two calls parse trivially and cost one
        extra process.
        """
        files = paths if paths is not None else self.files(from_tree, to_tree)
        if not files:
            return []
        statuses = self._statuses(from_tree, to_tree, files)
        patches = self._patches(from_tree, to_tree, files, context)
        diffs: list[FileDiff] = []
        for path in files:
            patch = patches.get(path, "")
            diffs.append(
                FileDiff(
                    path=path,
                    status=statuses.get(path, STATUS_MODIFIED),
                    additions=_count(patch, "+"),
                    deletions=_count(patch, "-"),
                    patch=patch,
                )
            )
        return diffs

    def _statuses(self, from_tree: str, to_tree: str, files: list[str]) -> dict[str, str]:
        code, out = _run(
            ["diff", "--raw", "-z", "--no-abbrev", from_tree, to_tree, "--", *files],
            git_dir=self.git_dir,
        )
        if code != 0:
            return {}
        parts = _text(out).split("\0")
        statuses: dict[str, str] = {}
        i = 0
        while i + 1 < len(parts):
            head = parts[i]
            if not head.startswith(":"):
                i += 1
                continue
            fields = head[1:].split()
            i += 2
            if len(fields) < 5:
                continue
            code_field = fields[4]
            statuses[parts[i - 1]] = (
                STATUS_ADDED
                if code_field.startswith("A")
                else STATUS_DELETED
                if code_field.startswith("D")
                else STATUS_MODIFIED
            )
        return statuses

    def _patches(
        self, from_tree: str, to_tree: str, files: list[str], context: int
    ) -> dict[str, str]:
        code, out = _run(
            ["diff", "--no-color", f"--unified={context}", from_tree, to_tree, "--", *files],
            git_dir=self.git_dir,
        )
        if code != 0:
            return {}
        patches: dict[str, str] = {}
        for block in _text(out).split("diff --git "):
            if not block.strip():
                continue
            header = block.splitlines()[0] if block else ""
            path = _path_from_diff_header(header)
            if path:
                patches[path] = ("diff --git " + block).rstrip("\n")
        return patches

    # ------------------------------------------------------------------
    # restore
    # ------------------------------------------------------------------

    def restore(self, files: dict[str, str]) -> RestoreReport:
        """Restore each path from the tree that maps to it.

        A path absent from its tree is deleted. Paths outside ``files`` are
        untouched, so restoring a handful of files never disturbs the rest of
        the worktree. Paths that escape the project root are refused.
        """
        out = RestoreReport()
        for rel, tree in files.items():
            target = self._safe_path(rel)
            if target is None:
                out.failed.append(rel)
                continue
            entry = self._tree_entry(tree, rel)
            if entry is None:
                if target.exists() or target.is_symlink():
                    try:
                        target.unlink()
                        out.deleted.append(rel)
                    except OSError:
                        out.failed.append(rel)
                else:
                    out.skipped.append(rel)
                continue
            mode, oid = entry
            payload = self._read_blob(oid)
            if payload is None:
                out.failed.append(rel)
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                os.chmod(target, 0o755 if mode == "100755" else 0o644)
                out.restored.append(rel)
            except OSError:
                log.exception("failed to restore %s", rel)
                out.failed.append(rel)
        return out

    def _safe_path(self, rel: str) -> Path | None:
        """Resolve ``rel`` inside the project, or ``None`` if it escapes."""
        if not rel or rel.startswith("/"):
            return None
        try:
            root = Path(self.cwd).resolve()
            target = (root / rel).resolve()
            target.relative_to(root)
        except (OSError, ValueError):
            return None
        return target

    def _tree_entry(self, tree: str, rel: str) -> tuple[str, str] | None:
        """``(mode, blob_oid)`` for ``rel`` in ``tree``, or ``None`` if absent."""
        code, out = _run(["ls-tree", tree, "--", rel], git_dir=self.git_dir)
        if code != 0:
            return None
        line = _text(out).strip()
        if not line:
            return None
        meta, _, _path = line.partition("\t")
        parts = meta.split()
        if len(parts) < 3 or parts[1] != "blob":
            return None
        return parts[0], parts[2]

    def _read_blob(self, oid: str) -> bytes | None:
        code, out = _run(["cat-file", "blob", oid], git_dir=self.git_dir, binary=True)
        if code != 0 or not isinstance(out, bytes):
            return None
        return out

    def checkout(self, tree: str) -> None:
        """Replace the whole worktree with ``tree`` (used only for full resets)."""
        code, out = _run(["ls-tree", "-r", "--name-only", "-z", tree], git_dir=self.git_dir)
        if code != 0:
            return
        wanted = {item for item in _text(out).split("\0") if item}
        for rel in wanted:
            self.restore({rel: tree})
        current = self.files(tree, self.capture() or tree)
        self.restore({rel: tree for rel in current if rel not in wanted})


_store_cache: dict[str, SnapshotStore] = {}


def get_store(cwd: str) -> SnapshotStore:
    """Process-wide store per project directory."""
    key = str(Path(cwd).resolve())
    store = _store_cache.get(key)
    if store is None:
        store = _store_cache[key] = SnapshotStore(key)
    return store
