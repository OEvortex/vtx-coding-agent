"""Fuzzy subsequence matching for the completion providers and the picker list.

Ported from pi-mono's fuzzy matcher: a query matches when its characters
appear in the candidate in order, scoring rewards consecutive runs and
word-boundary starts and penalises gaps and late matches.

Scores are inverted relative to pi-mono (higher is better here) so the
descending sorts already in the callers keep working.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar

T = TypeVar("T")

NO_MATCH = 0.0

_WORD_BOUNDARY = re.compile(r"[\s\-_./:]")
_TOKEN_SPLIT = re.compile(r"[\s/]+")


def fuzzy_match(query: str, text: str) -> tuple[float, Sequence[int]]:
    """Score ``query`` as a subsequence of ``text``.

    Returns ``(score, positions)``; the score is ``NO_MATCH`` (and the
    positions empty) when the query is not a subsequence. An empty query
    matches everything with a neutral score.
    """
    if not query:
        return (1.0, [])
    if len(query) > len(text):
        return (NO_MATCH, ())

    needle = query.lower()
    haystack = text.lower()

    positions: list[int] = []
    score = 0.0
    last_index = -1
    consecutive = 0
    cursor = 0

    for char in needle:
        index = haystack.find(char, cursor)
        if index == -1:
            return (NO_MATCH, ())

        if last_index >= 0 and index == last_index + 1:
            consecutive += 1
            score += consecutive * 5
        else:
            consecutive = 0
            if last_index >= 0:
                score -= (index - last_index - 1) * 2

        if index == 0 or _WORD_BOUNDARY.match(haystack[index - 1]):
            score += 10

        score -= index * 0.1
        positions.append(index)
        last_index = index
        cursor = index + 1

    if needle == haystack:
        score += 100
    return (score, tuple(positions))


def fuzzy_filter[T](items: Iterable[T], query: str, get_text: Callable[[T], str]) -> list[T]:
    """Filter and rank ``items`` by fuzzy match against ``get_text(item)``.

    The query is split on whitespace and ``/``; every token must match. Ties
    keep the incoming order, so callers that care should pre-sort.
    """
    tokens = [token for token in _TOKEN_SPLIT.split(query.strip()) if token]
    if not tokens:
        return list(items)

    scored: list[tuple[float, T]] = []
    for item in items:
        text = get_text(item)
        total = 0.0
        for token in tokens:
            score, _ = fuzzy_match(token, text)
            if score == NO_MATCH:
                break
            total += score
        else:
            scored.append((total, item))

    scored.sort(key=lambda pair: -pair[0])
    return [item for _, item in scored]
