"""Prompt history, shared with ctx's own TUI.

Both front ends recall the same prompts because both read the same rows in
``ctx.db``: an up arrow recalls what you asked in either one, in the order you
asked it. The cap matches ctx's so neither side trims more than the other.
"""

from __future__ import annotations

import time

#: How many prompts to keep, newest last. ctx's own limit, so both agree.
MAX_ENTRIES = 50


def _store():
    from ctx.core import store

    return store


def load(limit: int = MAX_ENTRIES) -> list[str]:
    """The prompts kept from earlier runs, oldest first."""
    try:
        entries = _store().recent_prompts(limit)
    except Exception:
        return []
    return list(reversed(entries))


def save(entries: list[str], limit: int = MAX_ENTRIES) -> None:
    """Rewrite the history, keeping the newest ``limit`` prompts."""
    try:
        _store().replace_prompts(entries[-limit:], limit)
    except Exception:
        return


def append(entry: str, existing: list[str], limit: int = MAX_ENTRIES) -> list[str]:
    """``existing`` with ``entry`` added once at the end, and saved."""
    text = entry.strip()
    if not text:
        return existing
    # Repeating the last prompt should not fill history with duplicates, but
    # an older prompt asked again is worth keeping where it was asked.
    if existing and existing[-1] == text:
        return existing
    entries = [*existing, text]
    if len(entries) > limit:
        entries = entries[-limit:]
    try:
        _store().append_prompt(text, time.time(), limit)
    except Exception:
        return existing
    return entries
