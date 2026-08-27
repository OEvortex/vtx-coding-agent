"""Git branch resolution for Vtx.

Re-exports from :mod:`vtx.core.git_branch`.
"""

from __future__ import annotations

from vtx.core.git_branch import (
    GitBranchState,
    _parse_branch_name,
    _run_git_head,
    _run_git_symbolic_ref,
    clear_git_branch_cache,
    resolve_git_branch,
)

__all__ = [
    "GitBranchState",
    "_parse_branch_name",
    "_run_git_head",
    "_run_git_symbolic_ref",
    "clear_git_branch_cache",
    "resolve_git_branch",
]
