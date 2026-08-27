"""GitHub CLI integration for Vtx.

Re-exports from :mod:`vtx.core.gh_cli`.
"""

from __future__ import annotations

from vtx.core.gh_cli import PullRequest, is_available, list_pull_requests

__all__ = ["PullRequest", "is_available", "list_pull_requests"]
