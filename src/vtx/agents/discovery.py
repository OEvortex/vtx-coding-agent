"""Discovery for switchable handoff agents (``.vtx/agent/<name>.py``).

Mirrors the extension discovery pattern in :mod:`vtx.extensions`. Two scopes,
project-local first, then global; project wins on name collision.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..config import get_config_dir

# Directories in priority order. Project first, global second.
PROJECT_AGENT_DIRNAME = ".vtx"
PROJECT_AGENT_SUBDIR = "agent"
GLOBAL_AGENT_SUBDIR = "agent"


def _agent_stem(p: Path) -> str:
    """Return the agent's name from a path (file stem or package dir name)."""
    if p.is_dir():
        return p.name
    return p.stem


def _candidate_paths(agent_dir: Path) -> list[Path]:
    """Return ``.py`` files and package ``__init__.py`` entries in ``agent_dir``."""
    if not agent_dir.is_dir():
        return []
    found: list[Path] = []
    for entry in sorted(agent_dir.iterdir()):
        if entry.is_file() and entry.suffix == ".py" and entry.name != "__init__.py":
            found.append(entry)
        elif entry.is_dir() and (entry / "__init__.py").is_file():
            found.append(entry / "__init__.py")
    return found


def find_agent_paths(
    *, cwd: str, configured: Iterable[str] | None = None, agent_dir: Path | None = None
) -> list[Path]:
    """Resolve agent paths to load, in priority order.

    Discovery order (later wins on name collision because it comes last in
    the returned list, and the loader dedupes by name with "last seen wins"):

    1. Project-local ``<cwd>/.vtx/agent/*.py`` and packages, walked up to
       the git root (inclusive). Closer files win.
    2. Global ``~/.vtx/agent/*.py`` and packages.
    3. Explicit ``configured`` paths (CLI ``--agent-file`` or
       ``agent_files:`` config list).
    """
    resolved_agent_dir = agent_dir or (get_config_dir() / GLOBAL_AGENT_SUBDIR)
    configured_paths = [Path(p).expanduser() for p in (configured or [])]

    seen_stems: set[str] = set()
    ordered: list[Path] = []

    def _add(p: Path) -> None:
        try:
            resolved = p.resolve()
        except OSError:
            return
        if not resolved.exists():
            return
        stem = _agent_stem(resolved)
        # "last seen wins": keep the first one we see, drop later duplicates.
        # This matches the skills/extensions discovery behavior
        # (project beats global, configured beats auto-discovered).
        if stem in seen_stems:
            return
        seen_stems.add(stem)
        ordered.append(resolved)

    # Walk up to the git root, inclusive, so a deeper project .vtx/agent wins.
    project_dirs: list[Path] = []
    cur = Path(cwd).resolve()
    while True:
        candidate = cur / PROJECT_AGENT_DIRNAME / PROJECT_AGENT_SUBDIR
        if candidate.is_dir():
            project_dirs.append(candidate)
        # Stop at filesystem root or when we leave the git working tree.
        if (cur / ".git").exists():
            break
        parent = cur.parent
        if parent == cur:
            break
        cur = parent

    for d in project_dirs:
        for p in _candidate_paths(d):
            _add(p)

    global_dir = resolved_agent_dir
    if global_dir.is_dir():
        for p in _candidate_paths(global_dir):
            _add(p)

    for cp in configured_paths:
        if cp.is_dir():
            if (cp / "__init__.py").is_file():
                _add(cp / "__init__.py")
                continue
            for p in _candidate_paths(cp):
                _add(p)
        elif cp.exists():
            _add(cp)

    return ordered


__all__ = ["find_agent_paths"]
