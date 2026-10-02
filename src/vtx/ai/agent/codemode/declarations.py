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


def is_mcp_result_schema(schema: object) -> bool:
    """Whether ``schema`` is an MCP ``CallToolResult`` envelope.

    Recognized structurally: an array-of-object ``content``, a boolean
    ``isError``, and an object ``_meta``. Structural rather than nominal so this
    module stays free of any dependency on :mod:`vtx.mcp` -- the dependency
    layering puts ``vtx.mcp`` above ``vtx.ai``, and a protocol marker does not
    justify inverting it. ``vtx.mcp`` builds the shape; this recognizes it.

    Takes ``object`` rather than a ``Mapping`` because the value arrives from a
    JSON schema on an untrusted tool, where a string or a list is as likely as a
    dict and must be answered with ``False`` rather than an ``AttributeError``
    that would surface as a sandbox failure.
    """
    if not isinstance(schema, Mapping):
        return False
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return False
    content = properties.get("content")
    if not isinstance(content, Mapping) or content.get("type") != "array":
        return False
    items = content.get("items")
    return isinstance(items, Mapping) and items.get("type") == "object"


def structured_content_schema(schema: object) -> Mapping[str, Any] | bool | None:
    """The ``structuredContent`` schema inside a ``CallToolResult`` envelope.

    ``True`` when the field is declared with no shape, and ``None`` when the
    schema is not a ``CallToolResult`` at all -- including when it is not a
    schema-shaped object at all.
    """
    if not isinstance(schema, Mapping) or not is_mcp_result_schema(schema):
        return None
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return None
    declared = properties.get("structuredContent")
    if isinstance(declared, bool):
        return declared
    if isinstance(declared, Mapping):
        return {str(key): value for key, value in declared.items()}
    # Declared with something that is not a schema at all. The envelope is real,
    # so it still gets named -- there is just no inner shape to print.
    return True


#: The content-block types a script can find in ``result["content"]``. Printed
#: once, under the catalog, and only when something actually uses it -- a
#: preamble nobody reads is context the model pays for on every turn.
MCP_TYPES_PREAMBLE = """\
MCP tools return a CallToolResult, not a string:
    result["content"]        list of blocks: {"type": "text"|"image", ...}
    result["structuredContent"]  the tool's own JSON, when it declares an output schema
    result["isError"]        True when the call failed -- check it and read
                             result["content"][0]["text"] for why
Use structuredContent when it is there; it is the typed data. `_meta` is stripped."""


def output_type(schema: Mapping[str, Any] | None) -> str:
    """The return annotation for one tool.

    An MCP tool's value is a ``CallToolResult``, and printing that as
    ``dict[str, Any]`` would understate what the script actually receives -- it
    would not say the result can carry structured data or that ``isError`` is
    there to be branched on, which are the two things a script most needs to
    know. So the envelope is named, and the tool's declared output shape is
    spelled into it.
    """
    if schema is None:
        return "Any"
    structured = structured_content_schema(schema)
    if structured is None:
        return type_name(schema)
    if not isinstance(structured, Mapping):
        # `True` (declared, no shape) and `False` (a schema that accepts nothing)
        # both render as the bare envelope rather than an invented shape.
        return "CallToolResult"
    rendered = type_name(structured)
    return "CallToolResult" if rendered == "Any" else f"CallToolResult[{rendered}]"


def render_signature(tool: CodemodeTool) -> str:
    """Render one tool as a callable signature the model can copy.

    Per-field descriptions ride along as ``#`` comments rather than a docstring
    per parameter, because the whole catalog has to fit a token budget and a
    docstring per field costs more than the fact it conveys. The tool's own
    description is the docstring.
    """
    identifier = tool.identifier()
    output = output_type(tool.output_schema or None)
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
    the rest of the harness uses.

    **Tools are grouped by namespace and the budget is spent fairly across
    groups.** A single global cheapest-first pass is wrong once the tool set has
    sources: with two hundred MCP tools on one server and a dozen built-ins, the
    twelve are cheap and go in first, the server's tools fill the rest, and a
    second server's tools are left out entirely with no sign they exist. So
    each group takes turns, cheapest-first within its turn, and a group that
    cannot afford its next entry drops out while the others continue. Every
    group is represented before any group is complete.
    """
    if not tools:
        return "", True

    groups = _group_by_namespace(tools)
    budget = max(0, budget_tokens)
    shown = _allocate(groups, budget)

    sections: list[str] = []
    for namespace, entries in groups:
        visible = [entry for entry in entries if entry.tool.name in shown]
        label = f"{namespace} ({len(entries)} tool{'' if len(entries) == 1 else 's'}"
        if len(visible) == len(entries):
            label += ")"
        elif visible:
            label += f", {len(visible)} shown)"
        else:
            label += ", none shown)"
        heading = f"# {label}"
        if namespace_description(entries):
            heading += f"\n{namespace_description(entries)}"
        sections.append(
            "\n\n".join([heading, *(entry.text for entry in visible)]) if visible else heading
        )

    total = sum(len(entries) for _, entries in groups)
    body = "\n\n".join(sections)
    if any(tool.output_schema and is_mcp_result_schema(tool.output_schema) for tool in tools):
        # Only when a tool actually returns one, for the same reason the catalog
        # is partial-reporting rather than always-partial: text the model is not
        # using is text it pays for.
        body = f"{body}\n\n{MCP_TYPES_PREAMBLE}" if body else MCP_TYPES_PREAMBLE
    return body, len(shown) == total


class _Entry(NamedTuple):
    tool: CodemodeTool
    text: str
    cost: int


def _group_by_namespace(tools: Sequence[CodemodeTool]) -> list[tuple[str, list[_Entry]]]:
    """Group tools under their namespace, unnamespaced first, then by name.

    Order is deterministic and the ungrouped tools come first, so the most
    fundamental surface is never the one squeezed out of the catalog.
    """
    plain: list[_Entry] = []
    grouped: dict[str, list[_Entry]] = {}
    for tool in tools:
        text = render_signature(tool)
        entry = _Entry(tool, text, len(text) // 4 + 1)
        namespace = tool.namespace or ""
        if namespace:
            grouped.setdefault(namespace, []).append(entry)
        else:
            plain.append(entry)

    groups: list[tuple[str, list[_Entry]]] = [("", plain)] if plain else []
    groups.extend((name, grouped[name]) for name in sorted(grouped))
    return groups


def namespace_description(entries: list[_Entry]) -> str | None:
    """The group's own one-line description, if any tool in it declares one.

    Taken from the first tool that has one, so an MCP server's ``instructions``
    reaches the model as the header for its tools. That is the server describing
    its own capability, which is better than anything inferred here -- and it
    costs nothing when no script is written.
    """
    for entry in entries:
        if entry.tool.namespace_description:
            return entry.tool.namespace_description
    return None


def _allocate(groups: list[tuple[str, list[_Entry]]], budget: int) -> set[str]:
    """Choose which entries fit the budget, round-robin across groups.

    Cheapest-first *within* each group's turn, and every group gets a turn
    before any group gets a second. That is the property a flat pass lacks: a
    group with twenty expensive tools cannot crowd out a group with three cheap
    ones, which is exactly the failure that makes an MCP server's tools vanish
    from the catalog when built-ins are present.
    """
    queues = [sorted(entries, key=lambda e: (e.cost, e.tool.name)) for _, entries in groups]
    shown: set[str] = set()
    remaining = budget
    active = [queue for queue in queues if queue]
    while active:
        still: list[list[_Entry]] = []
        for queue in active:
            entry = queue[0]
            if entry.cost > remaining:
                # Out of budget. The group stops here; the others carry on, so a
                # namespace that cannot afford anything does not consume the turn
                # of a namespace that can.
                continue
            remaining -= entry.cost
            shown.add(entry.tool.name)
            queue.pop(0)
            if queue:
                still.append(queue)
        active = still
    return shown
