"""Tests for BM25 tool discovery and the kernel's cached catalog.

The RLM prompt documents the pre-bound helpers in prose and does not enumerate
every tool, so a cell that wants an unnamed capability has to guess. These cover
the ranker (pure, no host) and the kernel-side cache (no subprocess).

pi's codemode is the reference: it ships BM25 ``searchTools`` plus a
``describeTool`` lookup, and the gap it closes is the same one here — a wrong
tool guess costs a ``[bridge:unknown_tool]`` round trip and a re-read.
"""

from __future__ import annotations

import pytest

from vtx.ai.agent.rlm import repl as repl_module
from vtx.ai.agent.rlm.toolsearch import ToolDocument, rank, tool_document


def _doc(name: str, description: str = "", parameters: str = "") -> ToolDocument:
    return ToolDocument(name=name, description=description, parameters=parameters)


CORPUS = [
    _doc("read", "Read a file from disk.", "path offset limit"),
    _doc("write", "Write content to a file.", "path content"),
    _doc("edit", "Exact search-and-replace edit of a file.", "path old new"),
    _doc("bash", "Run a shell command.", "command timeout"),
    _doc("web_search", "Search the web and return results.", "query num_results"),
    _doc("fetch_webpage", "Fetch a URL and return its content as text.", "url"),
]


def test_exact_name_ranks_first():
    matches = rank("read", CORPUS)
    assert matches[0].name == "read"


def test_natural_language_reaches_the_right_tool():
    """A task description, not a tool name, is what the model actually has."""
    matches = rank("search the web for news articles", CORPUS)
    assert matches[0].name == "web_search"


def test_url_fetches_beat_web_search():
    matches = rank("fetch the contents of a url", CORPUS)
    assert matches[0].name == "fetch_webpage"


def test_snake_case_name_matches_from_prose():
    matches = rank("I need to replace text in a file", CORPUS)
    assert matches[0].name == "edit"


def test_parameter_names_are_searchable():
    """A caller thinks "the tool that takes a query", not "properties"."""
    documents = [_doc("alpha", "A tool.", ""), _doc("beta", "A tool.", "search_term num_results")]
    matches = rank("search_term", documents)
    assert matches[0].name == "beta"


def test_no_match_returns_empty_not_everything():
    """An empty result must mean "nothing matched", not "everything, ranked"."""
    assert rank("kubernetes helm chart", CORPUS) == []


def test_empty_query_returns_nothing():
    assert rank("", CORPUS) == []
    assert rank("   ", CORPUS) == []


def test_stopword_only_query_returns_nothing():
    assert rank("the and of", CORPUS) == []


def test_empty_corpus_returns_nothing():
    assert rank("read", []) == []


def test_limit_is_respected():
    assert len(rank("tool file", CORPUS, limit=2)) <= 2


def test_zero_limit_returns_nothing():
    assert rank("read", CORPUS, limit=0) == []


def test_scores_are_ordered_descending():
    matches = rank("file", CORPUS, limit=10)
    scores = [match.score for match in matches]
    assert scores == sorted(scores, reverse=True)


def test_ties_break_on_shorter_name():
    """Equal scores mean identical term matches; prefer the specific name."""
    documents = [_doc("a_much_longer_name_here", "widget", ""), _doc("widget", "widget", "")]
    matches = rank("widget", documents)
    assert matches[0].name == "widget"


def test_camel_case_is_split():
    documents = [_doc("runBash", "Execute a shell command.", "")]
    assert rank("bash", documents)[0].name == "runBash"


def test_underscored_name_matches_whole_and_parts():
    documents = [_doc("web_search", "", "")]
    assert rank("web_search", documents)[0].name == "web_search"
    assert rank("search", documents)[0].name == "web_search"


def test_single_letter_tokens_are_ignored():
    """A one-char token is noise; it would match every doc containing it."""
    documents = [_doc("x", "an x in a box", ""), _doc("y", "a box for y", "")]
    assert rank("x", documents) == []


def test_term_in_every_document_still_scores():
    """The 1+ in the IDF keeps a universal term positive instead of dropping it."""
    documents = [_doc("alpha", "shared token", ""), _doc("beta", "shared token", "")]
    matches = rank("shared", documents)
    assert {match.name for match in matches} == {"alpha", "beta"}


def test_document_frequency_breaks_ties_toward_the_rare_term():
    """A term in one document must outrank a term in all of them."""
    documents = [_doc("common", "common", ""), _doc("rare", "common distinctive", "")]
    assert rank("distinctive", documents)[0].name == "rare"


class _FakeTool:
    """A tool with an already-resolved schema, as ``ToolDefinition`` has."""

    def __init__(self, name: str, description: str, parameters: object = None) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters


class _FakePydanticTool:
    """A tool exposing a params model, as ``BaseTool`` does."""

    def __init__(self, name: str, description: str, params: object) -> None:
        self.name = name
        self.description = description
        self.params = params


class _FakeParams:
    @staticmethod
    def model_json_schema() -> dict:
        return {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        }


def test_tool_document_reads_pydantic_params():
    document = tool_document(_FakePydanticTool("search", "Search things.", _FakeParams))
    assert document.name == "search"
    assert "query" in document.parameters
    assert "limit" in document.parameters


def test_tool_document_reads_a_plain_parameters_attribute():
    document = tool_document(
        _FakeTool("read", "Read.", {"type": "object", "properties": {"path": {}}})
    )
    assert "path" in document.parameters


def test_tool_document_survives_a_broken_schema():
    class _BrokenParams:
        @staticmethod
        def model_json_schema() -> dict:
            raise RuntimeError("cannot generate")

    document = tool_document(_FakePydanticTool("x", "d", _BrokenParams))
    assert document.name == "x"
    # Still findable by name and description rather than dropped from the index.
    assert document.parameters == ""


def test_tool_document_with_no_schema_at_all():
    document = tool_document(_FakeTool("bare", "No schema."))
    assert document.name == "bare"
    assert document.parameters == ""


def test_tool_document_flattens_nested_and_union_properties():
    schema = {
        "type": "object",
        "properties": {
            "outer": {"type": "object", "properties": {"inner_name": {}}},
            "choice": {"anyOf": [{"type": "object", "properties": {"alt_name": {}}}]},
        },
    }
    document = tool_document(_FakeTool("t", "d", schema))
    assert "inner_name" in document.parameters
    assert "alt_name" in document.parameters


def test_tool_document_terminates_on_a_self_referential_schema():
    """A cycle would otherwise spin the property-name walk forever."""
    schema: dict = {"type": "object", "properties": {}}
    schema["properties"]["self"] = schema
    document = tool_document(_FakeTool("t", "d", schema))
    assert "self" in document.parameters


# --- kernel-side discovery cache -------------------------------------------


CATALOG = [
    {
        "name": "read",
        "description": "Read a file from disk.",
        "parameters": {"type": "object", "properties": {"path": {}}},
    },
    {
        "name": "web_search",
        "description": "Search the web and return results.",
        "parameters": {"type": "object", "properties": {"query": {}}},
    },
]


@pytest.fixture(autouse=True)
def _clear_cache():
    repl_module._clear_tool_discovery()
    yield
    repl_module._clear_tool_discovery()


def test_find_tools_ranks_the_cached_catalog(monkeypatch):
    monkeypatch.setattr(repl_module, "call_tool", lambda name: {"tools": CATALOG})
    found = repl_module._search_tools("search the web", limit=8)
    assert found[0]["name"] == "web_search"
    assert found[0]["description"]


def test_describe_tool_returns_the_declaration(monkeypatch):
    """The raw JSON schema, so the model can build a correct call directly."""
    monkeypatch.setattr(repl_module, "call_tool", lambda name: {"tools": CATALOG})
    described = repl_module._describe_tool("read")
    assert described is not None
    assert described["name"] == "read"
    assert "path" in described["parameters"]["properties"]


def test_describe_tool_returns_none_for_an_unknown_name(monkeypatch):
    monkeypatch.setattr(repl_module, "call_tool", lambda name: {"tools": CATALOG})
    assert repl_module._describe_tool("nope") is None


def test_catalog_is_fetched_once(monkeypatch):
    calls: list[str] = []

    def counting(name: str) -> dict:
        calls.append(name)
        return {"tools": CATALOG}

    monkeypatch.setattr(repl_module, "call_tool", counting)
    repl_module._search_tools("read", 8)
    repl_module._search_tools("web", 8)
    repl_module._describe_tool("read")
    assert len(calls) == 1, "the catalog must be cached across searches"


def test_clearing_the_cache_refetches(monkeypatch):
    calls: list[str] = []

    def counting(name: str) -> dict:
        calls.append(name)
        return {"tools": CATALOG}

    monkeypatch.setattr(repl_module, "call_tool", counting)
    repl_module._search_tools("read", 8)
    repl_module._clear_tool_discovery()
    repl_module._search_tools("read", 8)
    assert len(calls) == 2


def test_a_bridge_failure_degrades_to_no_results(monkeypatch):
    """Discovery is best-effort: it must not take down the cell that asked."""

    def boom(name: str) -> dict:
        raise RuntimeError("host bridge unavailable")

    monkeypatch.setattr(repl_module, "call_tool", boom)
    assert repl_module._search_tools("read", 8) == []
    assert repl_module._describe_tool("read") is None


def test_a_malformed_reply_degrades_to_no_results(monkeypatch):
    monkeypatch.setattr(repl_module, "call_tool", lambda name: {"tools": "not-a-list"})
    assert repl_module._search_tools("read", 8) == []


def test_the_catalog_bridge_name_is_not_a_registered_tool():
    """It must never reach the model's tool list or the prompt."""
    from vtx.ai.agent.tools import get_all_tools

    assert repl_module._TOOL_CATALOG_BRIDGE not in get_all_tools()
