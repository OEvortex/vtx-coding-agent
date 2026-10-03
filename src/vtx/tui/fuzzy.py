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
# Stricter than _WORD_BOUNDARY: only these end a token cleanly. A hyphen, dot or
# underscore *continues* an identifier, so `claude-opus-5` must not be treated as
# a whole word inside `claude-opus-5-fast`.
_TOKEN_EDGE = re.compile(r"[\s/:]")
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

    # Prefer a contiguous run. Greedy subsequence walking anchors on the first
    # occurrence of each character, so "claude-opus-5" inside
    # "anthropic/claude-opus-5" starts at the "c" of *anthropic* and never
    # reaches the real run, which misplaced every boundary bonus. Nearly every
    # typed query is contiguous, so try that first and fall back to the
    # subsequence walk only when it is not.
    contiguous_at = haystack.find(needle)
    if contiguous_at != -1:
        positions: list[int] = list(range(contiguous_at, contiguous_at + len(needle)))
    else:
        positions = []
        cursor = 0
        for char in needle:
            index = haystack.find(char, cursor)
            if index == -1:
                return (NO_MATCH, ())
            positions.append(index)
            cursor = index + 1

    score = 0.0
    last_index = -1
    consecutive = 0

    for index in positions:
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
        last_index = index

    if positions:
        start, end = positions[0], positions[-1]
        # The query covering exactly one whole token ("claude-opus-5" in
        # "anthropic/claude-opus-5 openrouter") must outrank the same run inside
        # a longer name. The old `needle == haystack` bonus could never fire
        # here, because callers search "label description", so all twelve
        # `claude-opus-5*` ids tied on score and the winner was arbitrary.
        whole_token = (
            end - start + 1 == len(needle)
            and (start == 0 or bool(_TOKEN_EDGE.match(haystack[start - 1])))
            and (end == len(haystack) - 1 or bool(_TOKEN_EDGE.match(haystack[end + 1])))
        )
        if whole_token:
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
