"""Tests for the grammar-constrained sampling hook.

Grammar-constrained decoding makes a provider emit only text the grammar
accepts, so a malformed tool argument becomes impossible rather than merely
unlikely. The hook is per tool and per provider dialect, because support is
uneven: only the OpenAI Chat Completions path is wired, and a provider with no
entry in ``variants`` must be sent an ordinary unconstrained request rather than
a grammar in a dialect it does not speak.
"""

from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import BaseModel

from vtx.ai.base import ProviderConfig
from vtx.ai.providers.openai_sdk import OpenAISDKProvider
from vtx.ai.sdk.openai import _tool_param
from vtx.core.types import ConstrainedSampling, ToolDefinition

GRAMMAR = """
start: SOURCE
SOURCE: /[\\s\\S]+/
"""


def _provider(provider: str | None = "openai") -> OpenAISDKProvider:
    """A provider stub carrying only what ``_convert_tools`` reads.

    Constructing a real ``OpenAISDKProvider`` resolves credentials and builds an
    SDK client, neither of which these tests need; the conversion is a pure
    function of the tool definitions and the configured provider slug. The real
    unbound method is grafted on so the code under test is the shipped one.
    """
    stub = SimpleNamespace(config=ProviderConfig(provider=provider, model="gpt-test"))
    stub._convert_tools = partial(OpenAISDKProvider._convert_tools, stub)
    return cast(OpenAISDKProvider, stub)


def _tool(name: str = "code", **kwargs) -> ToolDefinition:
    return ToolDefinition(
        name=name, description="d", parameters={"type": "object", "properties": {}}, **kwargs
    )


# --- the type ---------------------------------------------------------------


def test_for_provider_matches_the_slug():
    sampling = ConstrainedSampling(variants={"openai": GRAMMAR})
    assert sampling.for_provider("openai") == GRAMMAR
    assert sampling.for_provider("OpenAI") == GRAMMAR
    assert sampling.for_provider("  openai  ") == GRAMMAR


def test_for_provider_is_case_and_space_insensitive():
    """Provider slugs arrive from config and catalogs with inconsistent casing."""
    sampling = ConstrainedSampling(variants={"openai": GRAMMAR})
    assert sampling.for_provider("OpenAI") == GRAMMAR


def test_for_provider_returns_none_for_an_unknown_provider():
    sampling = ConstrainedSampling(variants={"openai": GRAMMAR})
    assert sampling.for_provider("anthropic") is None
    assert sampling.for_provider(None) is None
    assert sampling.for_provider("") is None


def test_a_missing_dialect_is_not_a_fallback():
    """Applying one provider's dialect to another's wire format would misparse."""
    sampling = ConstrainedSampling(variants={"openai": GRAMMAR})
    assert sampling.for_provider("groq") is None


def test_constrained_sampling_defaults_to_unconstrained():
    assert _tool().constrained_sampling is None


def test_the_type_is_grammar_only():
    with pytest.raises(ValueError):
        ConstrainedSampling(type="json_schema", variants={"openai": GRAMMAR})


# --- provider conversion ----------------------------------------------------


def test_a_tool_without_a_grammar_is_unchanged():
    """The common case must produce byte-identical output to before."""
    converted = _provider()._convert_tools([_tool()])
    assert converted == [
        {
            "type": "function",
            "function": {
                "name": "code",
                "description": "d",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def test_a_grammar_reaches_the_function_object():
    converted = _provider()._convert_tools(
        [_tool(constrained_sampling=ConstrainedSampling(variants={"openai": GRAMMAR}))]
    )
    assert converted[0]["function"]["grammar"] == {"type": "lark", "definition": GRAMMAR}


def test_a_grammar_is_omitted_for_a_provider_without_a_dialect():
    """Silently sending a Lark grammar to a non-OpenAI endpoint would break it."""
    converted = _provider("groq")._convert_tools(
        [_tool(constrained_sampling=ConstrainedSampling(variants={"openai": GRAMMAR}))]
    )
    assert "grammar" not in converted[0]["function"]


def test_every_tool_is_converted():
    tools = [
        _tool("a"),
        _tool("b", constrained_sampling=ConstrainedSampling(variants={"openai": GRAMMAR})),
        _tool("c"),
    ]
    converted = _provider()._convert_tools(tools)
    assert [entry["function"]["name"] for entry in converted] == ["a", "b", "c"]
    assert "grammar" in converted[1]["function"]


def test_an_empty_grammar_string_is_still_sent():
    """Whether the provider accepts it is the provider's call, not ours."""
    converted = _provider()._convert_tools(
        [_tool(constrained_sampling=ConstrainedSampling(variants={"openai": ""}))]
    )
    assert converted[0]["function"]["grammar"] == {"type": "lark", "definition": ""}


# --- wire kwargs ------------------------------------------------------------


def test_kwargs_keep_the_typed_shape_without_a_grammar():
    param = _tool_param({"function": {"name": "n", "description": "d", "parameters": {}}})
    assert param == {
        "type": "function",
        "function": {"name": "n", "description": "d", "parameters": {}},
    }


def test_kwargs_preserve_the_grammar():
    """The SDK must not strip the field the provider put there."""
    param = _tool_param(
        {
            "function": {
                "name": "n",
                "description": "d",
                "parameters": {},
                "grammar": {"type": "lark", "definition": GRAMMAR},
            }
        }
    )
    assert param["function"]["grammar"] == {"type": "lark", "definition": GRAMMAR}


def test_kwargs_default_a_missing_description():
    param = _tool_param({"function": {"name": "n", "parameters": {}}})
    assert param["function"]["description"] == ""


# --- tool plumbing ----------------------------------------------------------


def test_get_tool_definitions_carries_the_declaration():

    from vtx.ai.agent.tools import get_tool_definitions
    from vtx.ai.agent.tools.base import BaseTool

    class _Params(BaseModel):
        code: str

    class _Grammatical(BaseTool):
        name = "grammatical"
        description = "d"
        params = _Params
        constrained_sampling = ConstrainedSampling(variants={"openai": GRAMMAR})

        async def execute(self, params, cancel_event=None, tool_call_id=None, on_output=None):
            return None

    class _Plain(BaseTool):
        name = "plain"
        description = "d"
        params = _Params

        async def execute(self, params, cancel_event=None, tool_call_id=None, on_output=None):
            return None

    defs = {d.name: d for d in get_tool_definitions([_Grammatical(), _Plain()])}
    assert defs["grammatical"].constrained_sampling is not None
    assert defs["plain"].constrained_sampling is None


def test_the_rlm_ipython_tool_is_unconstrained():
    """Documented as a deliberate choice, so it is pinned rather than assumed.

    The cell is free-form Python: a grammar that only rejected markdown fences
    would reject more valid cells than the failure mode it prevents is worth.
    The hook exists for tools whose arguments genuinely have a fixed shape.
    """
    from vtx.ai.agent.tools.ipython import IpythonTool

    assert getattr(IpythonTool, "constrained_sampling", None) is None
