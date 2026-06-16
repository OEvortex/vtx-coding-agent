"""Tests for the per-provider thinking/reasoning wire parameters.

The OpenAI SDK and Anthropic SDK translate ``GenerationConfig.thinking_level``
into the specific request fields documented for each provider family:

- OpenAI family (openai / openai-codex / openai-responses): ``reasoning_effort``
  for low/medium/high; structured ``reasoning: {effort: ...}`` for
  minimal/xhigh.
- OpenRouter: ``reasoning: {effort: ..., exclude: True}`` for none.
- DeepSeek: ``extra_body={"thinking": {"type": ...}}`` + ``reasoning_effort``
  mapped (low/medium -> high, xhigh -> max).
- Zhipu / GLM: ``extra_body={"thinking": {"type": enabled|disabled}}``.
- TokenRouter: ``reasoning: {effort: ...}`` (low/medium/high only).
- Generic OpenAI-compat gateway (airouter, kilo, opencode, ollama):
  ``reasoning_effort`` passed through.
- Anthropic: ``thinking: {type: "enabled", budget_tokens: N}`` for
  non-none; omitted entirely for none.
"""

from __future__ import annotations

import pytest

from vtx.llm.sdk.anthropic import AnthropicSDK
from vtx.llm.sdk.base import GenerationConfig, Message
from vtx.llm.sdk.openai import OpenAISDK


def _kwargs(slug: str | None, level: str | None) -> dict:
    sdk = OpenAISDK(api_key="x", base_url="https://example.com", provider_slug=slug)
    msgs = [Message(role="user", content="hi")]
    cfg = GenerationConfig(model="m", thinking_level=level)
    return sdk._build_kwargs(msgs, cfg)


def _payload(level: str | None) -> dict:
    sdk = AnthropicSDK(api_key="x", base_url="https://example.com")
    msgs = [Message(role="user", content="hi")]
    cfg = GenerationConfig(model="m", thinking_level=level)
    return sdk._build_payload(msgs, cfg)


# --- OpenAI family -----------------------------------------------------------


def test_openai_none_omits_reasoning() -> None:
    assert "reasoning_effort" not in _kwargs("openai", "none")
    assert "reasoning" not in _kwargs("openai", "none")


def test_openai_low_medium_high_uses_top_level_param() -> None:
    for level in ("low", "medium", "high"):
        kwargs = _kwargs("openai", level)
        assert kwargs.get("reasoning_effort") == level
        assert "reasoning" not in kwargs


def test_openai_minimal_uses_structured_form() -> None:
    kwargs = _kwargs("openai", "minimal")
    assert "reasoning_effort" not in kwargs
    assert kwargs.get("reasoning") == {"effort": "minimal"}


def test_openai_xhigh_uses_structured_form() -> None:
    kwargs = _kwargs("openai", "xhigh")
    assert "reasoning_effort" not in kwargs
    assert kwargs.get("reasoning") == {"effort": "xhigh"}


def test_openai_codex_uses_native_param() -> None:
    assert _kwargs("openai-codex", "high").get("reasoning_effort") == "high"
    assert "reasoning_effort" not in _kwargs("openai-codex", "none")


# --- OpenRouter --------------------------------------------------------------


def test_openrouter_none_sends_exclude() -> None:
    kwargs = _kwargs("openrouter", "none")
    assert kwargs.get("reasoning") == {"effort": "none", "exclude": True}


@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high", "xhigh"])
def test_openrouter_effort_levels(level: str) -> None:
    kwargs = _kwargs("openrouter", level)
    assert kwargs.get("reasoning") == {"effort": level}


# --- DeepSeek ----------------------------------------------------------------


def test_deepseek_none_disables_thinking() -> None:
    kwargs = _kwargs("deepseek", "none")
    assert kwargs.get("extra_body") == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in kwargs


@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high"])
def test_deepseek_maps_low_medium_to_high(level: str) -> None:
    kwargs = _kwargs("deepseek", level)
    assert kwargs.get("reasoning_effort") == "high"
    assert kwargs.get("extra_body") == {"thinking": {"type": "enabled"}}


def test_deepseek_xhigh_maps_to_max() -> None:
    kwargs = _kwargs("deepseek", "xhigh")
    assert kwargs.get("reasoning_effort") == "max"
    assert kwargs.get("extra_body") == {"thinking": {"type": "enabled"}}


# --- Zhipu / GLM -------------------------------------------------------------


def test_zhipu_none_disables_thinking() -> None:
    kwargs = _kwargs("zhipu", "none")
    assert kwargs.get("extra_body") == {"thinking": {"type": "disabled"}}


@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high", "xhigh"])
def test_zhipu_enables_thinking_for_any_non_none_level(level: str) -> None:
    kwargs = _kwargs("zhipu", level)
    assert kwargs.get("extra_body") == {"thinking": {"type": "enabled"}}


# --- TokenRouter -------------------------------------------------------------


def test_tokenrouter_none_omits_reasoning() -> None:
    assert "reasoning" not in _kwargs("tokenrouter", "none")


@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_tokenrouter_effort_levels(level: str) -> None:
    assert _kwargs("tokenrouter", level).get("reasoning") == {"effort": level}


# --- Generic OpenAI-compat gateway ------------------------------------------


def test_generic_gateway_passes_through() -> None:
    assert _kwargs("kilo", "none").get("reasoning_effort") is None
    assert _kwargs("kilo", "high").get("reasoning_effort") == "high"
    assert _kwargs("airouter", "low").get("reasoning_effort") == "low"


def test_unknown_slug_passes_through() -> None:
    # No slug at all (the SDK's default path) - pass through.
    assert "reasoning_effort" not in _kwargs(None, "none")
    assert _kwargs(None, "low").get("reasoning_effort") == "low"


# --- Anthropic ---------------------------------------------------------------


def test_anthropic_none_omits_thinking() -> None:
    assert "thinking" not in _payload("none")


def test_anthropic_none_keeps_other_fields() -> None:
    payload = _payload("none")
    assert payload.get("model") == "m"
    assert "messages" in payload


@pytest.mark.parametrize(
    ("level", "expected_budget"),
    [("minimal", 1024), ("low", 2048), ("medium", 4096), ("high", 8192), ("xhigh", 16384)],
)
def test_anthropic_levels_map_to_budget(level: str, expected_budget: int) -> None:
    payload = _payload(level)
    assert payload.get("thinking") == {"type": "enabled", "budget_tokens": expected_budget}


def test_anthropic_unknown_level_omits_thinking() -> None:
    assert "thinking" not in _payload("bogus")
