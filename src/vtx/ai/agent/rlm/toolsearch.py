"""BM25 ranking over the tool surface, for the RLM kernel's tool discovery.

The RLM prompt documents the pre-bound helpers in prose and does not enumerate
every tool, so a cell that wants a capability it was never shown has two bad
options: guess a name and eat a ``[bridge:unknown_tool]`` round trip, or read a
listing that is not in its context. This ranks the callable tools by a task
description so the model can search instead of guess.

BM25 rather than substring matching: tool names are short and descriptions are
prose, so a query like "search the web for news" must match ``web`` on
``web_search``/``fetch``-style terms without the query's own stopwords sinking
the ranking. Scoring is the standard Robertson/Sparck-Jones IDF with the usual
length normalisation; see the ``rank`` docstring for the formula.

The corpus is rebuilt per query rather than cached. The tool surface is a
handful of entries and changes only when the session reloads, so an index would
be a cache with a staleness bug attached.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any, NamedTuple

#: Standard BM25 term-saturation and length-normalisation constants. k1 bounds
#: how much a repeated term keeps helping; b controls how strongly a long
#: description is penalised relative to a terse one.
_BM25_K1 = 1.5
_BM25_B = 0.75

#: Words carrying no retrieval signal. Kept small on purpose: an aggressive list
#: drops terms the tool author used deliberately ("read", "list", "get").
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "the",
        "to",
        "use",
        "what",
        "when",
        "which",
        "with",
    }
)

#: Splits on anything that is not a word character, so ``web_search``,
#: ``read-file``, and ``readFile`` all yield their parts. Names are additionally
#: indexed whole (see :func:`_terms`) so an exact tool name still ranks first.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
#: Zero-width boundary before an uppercase letter, used to split camelCase.
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])([A-Z])")

#: Field weights. The name is the strongest signal, but a description carrying
#: the query's words is what disambiguates two similarly named tools.
_WEIGHT_NAME = 3.0
_WEIGHT_DESCRIPTION = 1.0
_WEIGHT_PARAMETERS = 0.5

DEFAULT_TOOL_SEARCH_LIMIT = 8


class ToolDocument(NamedTuple):
    """One indexable tool."""

    name: str
    description: str
    parameters: str


class ToolMatch(NamedTuple):
    name: str
    score: float


def tool_document(tool: Any) -> ToolDocument:
    """Build the searchable document for a tool.

    Accepts a ``BaseTool``, a ``ToolDefinition``, or anything exposing ``name``
    plus one of ``description`` / ``parameters`` / ``params``, so this works
    for built-ins, extension tools, and MCP tools without a per-source branch.
    ``parameters`` wins when both are present: an explicit schema is the
    already-resolved contract, while ``params`` needs a call to produce one.
    """
    name = str(getattr(tool, "name", "") or "")
    description = str(getattr(tool, "description", "") or "")
    return ToolDocument(
        name=name, description=description, parameters=_parameter_names(_schema_of(tool))
    )


def _schema_of(tool: Any) -> Any:
    """The tool's input schema, from ``parameters`` or by calling ``params``."""
    parameters = getattr(tool, "parameters", None)
    if parameters is not None:
        return parameters
    params_model = getattr(tool, "params", None)
    if params_model is None:
        return {}
    schema = getattr(params_model, "model_json_schema", None)
    if not callable(schema):
        return {}
    try:
        return schema() or {}
    except Exception:
        # A tool whose schema will not generate is still findable by name and
        # description; dropping it entirely would make it undiscoverable.
        return {}


def _parameter_names(schema: Any) -> str:
    """The schema's property names, which are what a caller searches by.

    A caller thinks "the tool that takes a query string", not "the tool whose
    input schema has a ``properties`` key", so the property names belong in the
    corpus. Flattened to names only: types and descriptions of nested objects
    add noise without adding retrievable terms.
    """
    names: list[str] = []
    stack: list[Any] = [schema]
    # Identity, not equality: a self-referential schema recurses forever under
    # ``==``, and a shared sub-schema is legitimately visited once.
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if not isinstance(node, dict) or id(node) in seen:
            continue
        seen.add(id(node))
        properties = node.get("properties")
        if isinstance(properties, dict):
            names.extend(str(key) for key in properties)
            # Descend into each property, so a nested object's own field names
            # are searchable: "the tool that takes a search term" has to reach a
            # term nested one level down.
            stack.extend(value for value in properties.values() if isinstance(value, dict))
        items = node.get("items")
        if isinstance(items, dict):
            stack.append(items)
        for key in ("anyOf", "oneOf", "allOf"):
            variants = node.get(key)
            if isinstance(variants, list):
                stack.extend(v for v in variants if isinstance(v, dict))
    return " ".join(names)


def _terms(text: str) -> list[str]:
    """Lowercase token list for one field, including split and whole forms.

    ``web_search`` and ``runBash`` both yield their whole form plus their parts.
    Keeping the whole form is what makes an exact tool-name query rank that tool
    first; the parts are what let a natural-language query reach it.
    """
    # Split camelCase before lowercasing, so ``runBash`` contributes ``bash``
    # rather than only the unmatchable ``runbash``.
    split = _CAMEL_RE.sub(r"_\1", text)
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(split.lower()):
        token = match.group(0)
        if len(token) > 1 and token not in _STOPWORDS:
            tokens.append(token)
    return tokens


def _weighted_terms(document: ToolDocument) -> list[str]:
    """The document's bag of terms with each field's weight applied.

    Weighting is applied by repeating terms, which keeps the scorer a plain
    term-frequency model: a term in the name counts three times, a term in a
    parameter name counts half. Cheaper and easier to reason about than a
    per-field score, and exact for the ranking it produces.
    """
    terms: list[str] = []
    for token in _terms(document.name):
        terms.extend([token] * int(_WEIGHT_NAME))
    for token in _terms(document.description):
        terms.extend([token] * int(_WEIGHT_DESCRIPTION))
    for token in _terms(document.parameters):
        terms.extend([token] * max(int(_WEIGHT_PARAMETERS), 1))
    return terms


def rank(
    query: str, documents: Iterable[ToolDocument], limit: int = DEFAULT_TOOL_SEARCH_LIMIT
) -> list[ToolMatch]:
    """Rank ``documents`` against ``query`` with BM25, best first.

    ``score = Σ_t idf(t) · (f(t,d)·(k1+1)) / (f(t,d) + k1·(1-b+b·|d|/avgdl))``

    with ``idf(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))``. The ``1 +`` keeps
    the IDF positive for a term in every document, which would otherwise score
    zero and silently drop a query term that happens to match all of them.

    A document that matches nothing scores zero and is excluded, so an empty
    result means "nothing matched" rather than "everything, arbitrarily".
    """
    query_terms = _terms(query)
    if not query_terms:
        return []
    docs = list(documents)
    if not docs:
        return []
    bags = [_weighted_terms(document) for document in docs]
    total = len(docs)
    avg_length = sum(len(bag) for bag in bags) / total if total else 0.0

    scores: list[float] = []
    for bag in bags:
        counts: dict[str, int] = {}
        for term in bag:
            counts[term] = counts.get(term, 0) + 1
        length = len(bag) or 1
        score = 0.0
        for term in query_terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            document_frequency = sum(1 for other in bags if term in other)
            idf = math.log(1 + (total - document_frequency + 0.5) / (document_frequency + 0.5))
            denominator = frequency + _BM25_K1 * (
                1 - _BM25_B + _BM25_B * length / (avg_length or 1)
            )
            score += idf * (frequency * (_BM25_K1 + 1)) / denominator
        scores.append(score)

    matches = [
        ToolMatch(name=document.name, score=score)
        for document, score in zip(docs, scores, strict=True)
        if score > 0
    ]
    # Name breaks ties: equal scores mean the two tools matched the same terms,
    # and the shorter name is the more specific one.
    matches.sort(key=lambda match: (-match.score, len(match.name)))
    return matches[: max(limit, 0)]


__all__ = ["DEFAULT_TOOL_SEARCH_LIMIT", "ToolDocument", "ToolMatch", "rank", "tool_document"]
