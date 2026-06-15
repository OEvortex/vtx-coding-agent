from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

from vtx import AVAILABLE_BINARIES


@dataclass(frozen=True)
class PullRequest:
    number: int
    branch: str
    title: str

    def chat_reference(self) -> str:
        description = _single_line_description(self.title)
        return f'PR#{self.number} {self.branch} "{description}"'


def _single_line_description(description: str) -> str:
    lines = description.splitlines()
    first_line = lines[0].strip() if lines else ""
    hidden_count = len(lines) - 1
    if hidden_count <= 0:
        return first_line
    return f"{first_line} ... ({hidden_count} lines hidden)"


_CACHE_TTL_SECONDS = 30.0
_cached_cwd: str | None = None
_cached_at: float = 0.0
_cached_prs: list[PullRequest] = []


def is_available() -> bool:
    return "gh" in AVAILABLE_BINARIES


def list_pull_requests(cwd: str = ".") -> list[PullRequest]:
    global _cached_at, _cached_cwd, _cached_prs

    if not is_available():
        return []

    now = time.monotonic()
    if _cached_cwd == cwd and now - _cached_at < _CACHE_TTL_SECONDS:
        return _cached_prs

    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--json", "number,headRefName,title", "--limit", "50"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except Exception:
        return []

    if result.returncode != 0:
        return []

    try:
        raw_prs = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    prs = [
        PullRequest(
            number=int(pr["number"]),
            branch=str(pr.get("headRefName") or ""),
            title=str(pr.get("title") or ""),
        )
        for pr in raw_prs
        if "number" in pr
    ]
    _cached_cwd = cwd
    _cached_at = now
    _cached_prs = prs
    return prs
