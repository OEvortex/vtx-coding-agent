"""Render tool declarations for the model, and the BM25 ranker that finds them.

This is the discovery layer, and it is the part that decides whether the whole
design is usable. Dumping every signature into the prompt is unaffordable at
scale, so:

- **Declarations** render JSON Schema into Python ``def``-shaped signatures the
  model can read. Schema field descriptions become docstring lines, and
  constraints TypeScript/Python cannot express ride along as tags.
- **The ranker** is BM25 over name plus description plus schema property names
  and their descriptions, so a query naming a *parameter* finds its tool.
  BM25 rather than substring matching because tool names are short and
  descriptions are prose: "search the web for news" must match ``web`` on
  ``web_search`` without the query's own stopwords sinking the ranking.
- **The caller decides what to inline.** :func:`render_declarations` takes an
  explicit budget and reports honestly whether the list it produced is
  complete, because a model that is told "here are all the tools" when the list
  is truncated will never search.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from vtx.ai.agent.codemode.types import CodemodeTool

#: Standard BM25 constants. ``k1`` bounds how much a repeated term keeps
#: helping; ``b`` controls how hard a long description is penalised against a
#: terse one.
_BM25_K1 = 1.5
_BM25_B = 0.75

#: Words carrying no retrieval signal. Kept small on purpose: an aggressive
#: list drops terms a tool author used deliberately ("read", "list", "get").
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
        "what",
        "with",
    }
)

#: Names the sandbox namespace already uses. A tool whose identifier would
#: collide is suffixed rather than allowed to shadow.
_DECLARATION_RESERVED = frozenset({"tools", "store", "load", "text", "print", "host_request"})

_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]*|[0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


class SearchMatch(NamedTuple):
    """One ranked tool."""

    tool: CodemodeTool
    score: float


def to_identifier(name: str) -> str:
    """Map a tool name onto a Python identifier.

    Non-identifier characters collapse to ``_``, camelCase is left intact
    because it is already a valid identifier, and a leading digit or a reserved
    word is suffixed. Deterministic and total, so the declaration text and the
    runtime namespace always agree.
    """
    identifier = _CAMEL_BOUNDARY.sub("_", name)
    identifier = re.sub(r"[^0-9a-zA-Z_]", "_", identifier)
    if not identifier:
        identifier = "tool"
    if identifier[0].isdigit():
        identifier = f"tool_{identifier}"
    if identifier in _DECLARATION_RESERVED:
        identifier = f"{identifier}_tool"
    return identifier


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase search terms, splitting camelCase first."""
    parts: list[str] = []
    for chunk in text.split():
        for piece in _CAMEL_BOUNDARY.sub(" ", chunk).split():
            parts.extend(_TOKEN_PATTERN.findall(piece))
    return [p.lower() for p in parts if p.lower() not in _STOPWORDS]


def _document(tool: CodemodeTool) -> list[str]:
    """Build the searchable document for one tool.

    Includes schema property names *and* their descriptions, which is why a
    query naming a parameter finds the tool. Without that, searching for
    "cursor" would miss ``list_issues`` even though ``after: cursor`` is right
    there in its schema.
    """
    text = [tool.name.replace("-", " ").replace("_", " "), tool.description]
    schema = tool.input_schema or {}
    for name, detail in _walk_schema(schema):
        text.append(name.replace("_", " "))
        if detail:
            text.append(detail)
    return _tokenize(" ".join(text))


def _walk_schema(node: Any, depth: int = 0) -> Iterable[tuple[str, str]]:
    """Yield ``(property name, description)`` pairs from a JSON Schema."""
    if depth > 8 or not isinstance(node, dict):
        return
    for name, detail in (node.get("properties") or {}).items():
        if isinstance(name, str):
            description = detail.get("description") if isinstance(detail, dict) else None
            yield name, description if isinstance(description, str) else ""
    for key in ("items", "additionalProperties"):
        detail = node.get(key)
        if isinstance(detail, dict):
            yield from _walk_schema(detail, depth + 1)


def rank(query: str, tools: Sequence[CodemodeTool], *, limit: int = 10) -> list[SearchMatch]:
    """Rank ``tools`` against ``query``.

    The corpus is rebuilt per query rather than cached: the tool surface is a
    handful of entries and changes only when the session reloads, so an index
    would be a cache with a staleness bug attached.
    """
    terms = _tokenize(query)
    if not terms:
        return []
    documents = {tool.name: _document(tool) for tool in tools}
    total = len(documents)
    if total == 0:
        return []

    frequencies: dict[str, int] = {}
    for terms_in_doc in documents.values():
        for term in set(terms_in_doc):
            frequencies[term] = frequencies.get(term, 0) + 1

    by_name = {tool.name: tool for tool in tools}
    scored: list[SearchMatch] = []
    for name, terms_in_doc in documents.items():
        if not terms_in_doc:
            continue
        score = 0.0
        length = len(terms_in_doc)
        for term in terms:
            count = terms_in_doc.count(term)
            if not count:
                continue
            containing = frequencies.get(term, 0)
            # Robertson/Sparck-Jones IDF with the usual +0.5 smoothing.
            idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
            denominator = count + _BM25_K1 * (1 - _BM25_B + _BM25_B * length / (length + _BM25_K1))
            score += idf * (count * (_BM25_K1 + 1)) / denominator
        if score > 0:
            scored.append(SearchMatch(by_name[name], score))

    scored.sort(key=lambda match: (-match.score, match.tool.name))
    return scored[:limit]


def _schema_lines(schema: Mapping[str, Any], indent: str) -> list[str]:
    """Render one JSON Schema object's properties as annotated parameters.

    Optional fields are annotated ``| None`` rather than with a ``?`` suffix.
    A ``?`` is the TypeScript spelling and this is a Python declaration: the
    model is going to copy it, and ``after?`` is not valid Python.
    """
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or ())
    if not isinstance(properties, dict) or not properties:
        return [f"{indent}**kwargs: Any"]

    lines: list[str] = []
    for name, detail in properties.items():
        if not isinstance(name, str):
            continue
        detail = detail if isinstance(detail, dict) else {}
        annotation = type_name(detail)
        if name not in required:
            annotation = f"{annotation} | None"
        lines.append(f"{indent}{name}: {annotation}")
    # A required name with no declared property is a schema the tool author
    # should fix, but rendering it as Any is more useful than dropping it.
    for name in sorted(required - set(properties)):
        lines.append(f"{indent}{name}: Any")
    return lines


def type_name(schema: Mapping[str, Any]) -> str:
    """Render a JSON Schema fragment as a Python type annotation.

    Unresolved constructs render as ``Any`` rather than an invented name. A
    declaration the model acts on must be a claim the host will honor; a type
    the runtime never checks is worse than an honest ``Any``.
    """
    if not isinstance(schema, Mapping):
        return "Any"
    for key in ("$ref", "oneOf", "anyOf", "allOf"):
        if key in schema:
            return "Any"
    kind = schema.get("type")
    if isinstance(kind, list):
        kinds = {k for k in kind if isinstance(k, str)}
        if "null" in kinds:
            remaining = sorted(kinds - {"null"})
            if len(remaining) == 1:
                return f"{type_name({'type': remaining[0]})} | None"
            return "Any"
        return "Any"
    if kind == "string":
        enum = schema.get("enum")
        if isinstance(enum, list) and enum and all(isinstance(v, str) for v in enum):
            return " | ".join(repr(v) for v in enum)
        return "str"
    if kind == "integer":
        return "int"
    if kind == "number":
        return "float"
    if kind == "boolean":
        return "bool"
    if kind == "null":
        return "None"
    if kind == "array":
        return f"list[{type_name(schema.get('items') or {})}]"
    if kind == "object":
        return "dict[str, Any]"
    return "Any"


def render_signature(tool: CodemodeTool) -> str:
    """Render one tool as a callable signature the model can copy.

    Per-field descriptions ride along as ``#`` comments rather than a docstring
    per parameter, because the whole catalog has to fit a token budget and a
    docstring per field costs more than the fact it conveys. The tool's own
    description is the docstring.
    """
    identifier = tool.identifier()
    output = type_name(tool.output_schema or {})
    parameters = _schema_lines(tool.input_schema or {}, "")
    lines = [
        f"def tools.{identifier}({', '.join(parameters)}) -> {output}:",
        f'    """{tool.description}"""',
    ]
    properties = (tool.input_schema or {}).get("properties") or {}
    if isinstance(properties, dict):
        for name, detail in properties.items():
            description = detail.get("description") if isinstance(detail, dict) else None
            if isinstance(description, str) and description:
                lines.append(f"    # {name}: {description}")
            default = detail.get("default") if isinstance(detail, dict) else None
            if default is not None:
                lines.append(f"    # {name} defaults to {default!r}")
    return "\n".join(lines)


def render_declarations(
    tools: Sequence[CodemodeTool], *, budget_tokens: int = 2000
) -> tuple[str, bool]:
    """Render tool declarations within a token budget.

    Returns ``(text, complete)``. ``complete`` is ``False`` when the budget
    could not fit every tool, and the caller must tell the model so -- a model
    told the list is exhaustive will never go looking for a search tool.

    The budget is estimated at four characters per token, the same heuristic
    the rest of the harness uses. Selection is round-robin over the tools in
    declaration order so a cheap tool is not starved by an expensive one that
    happens to sort earlier.
    """
    if not tools:
        return "", True

    rendered = [(tool, render_signature(tool)) for tool in tools]
    costs = [len(text) // 4 + 1 for _, text in rendered]
    budget = max(0, budget_tokens)

    # Cheapest-first, not declaration order, so one expensive tool cannot consume
    # the budget that a dozen cheap ones would have fit into. What the model is
    # missing is a count of tools, so maximizing that count is the goal.
    included: list[int] = []
    spent = 0
    for index in sorted(range(len(rendered)), key=lambda i: (costs[i], i)):
        cost = costs[index]
        if spent + cost > budget:
            break
        included.append(index)
        spent += cost

    if len(included) == len(rendered):
        return "\n\n".join(rendered[i][1] for i in range(len(rendered))), True

    included.sort()
    body = "\n\n".join(rendered[i][1] for i in included)
    return body, False
