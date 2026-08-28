"""Git branch resolution for Vtx.

Re-exports from :mod:`vtx.core.git_branch`.
"""

from __future__ import annotations

from vtx.core.git_branch import GitPaths, find_git_paths, resolve_git_branch

__all__ = ["GitPaths", "find_git_paths", "resolve_git_branch"]
