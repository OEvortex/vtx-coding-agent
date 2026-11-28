"""Who a board post calls on.

A waiting agent sleeps until somebody names it, so this is the doorbell. The
rules are the ones the models are told in the prompt: ``@name`` at a word
start, case-insensitive, not followed by more name characters; ``@all`` is
everyone; ``@group`` also reaches the numbered copies ``group-1``…
``group-n``. ``@#channel`` is a channel call and names no member.
"""

from __future__ import annotations

import re

#: A member name: letters, digits, ``_`` and ``-``. A leading ``@`` must not be
#: part of a longer word, an email, or another mention.
_NAME_RE = re.compile(r"(?<![\w@#])@([A-Za-z0-9][A-Za-z0-9_-]*)")

#: ``@#area`` calls everyone following a channel.
_CHANNEL_RE = re.compile(r"(?<![\w@#])@#([A-Za-z0-9][A-Za-z0-9_-]*)")

#: Broadcast name.
ALL = "all"

#: The coordinator's roster name.
COORDINATOR = "coordinator"


def mentions(text: str) -> set[str]:
    """Every name ``text`` calls on, lowercased, including :data:`ALL`."""
    return {m.group(1).lower() for m in _NAME_RE.finditer(text or "")}


def channel_calls(text: str) -> set[str]:
    """Every channel ``text`` calls on with ``@#name``, lowercased."""
    return {m.group(1).lower() for m in _CHANNEL_RE.finditer(text or "")}


def group_of(name: str) -> str:
    """``parser-2`` -> ``parser``; a bare name is its own group."""
    base, sep, suffix = (name or "").rpartition("-")
    if sep and suffix.isdigit() and base:
        return base
    return name


def addressed(text: str, name: str) -> bool:
    """Whether ``text`` calls on member ``name`` (by name, its group, or ``all``)."""
    called = mentions(text)
    if not called or not name:
        return False
    low = name.lower()
    if ALL in called:
        return True
    if low in called:
        return True
    group = group_of(low).lower()
    return group != low and group in called


def is_broadcast(text: str) -> bool:
    return ALL in mentions(text)


__all__ = [
    "ALL",
    "COORDINATOR",
    "addressed",
    "channel_calls",
    "group_of",
    "is_broadcast",
    "mentions",
]
