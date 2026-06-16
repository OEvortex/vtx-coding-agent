"""Tests for the per-provider thinking/reasoning wire parameters.

The OpenAI SDK and Anthropic SDK translate ``GenerationConfig.thinking_level``
into the specific request fields documented for each provider family:

- OpenAI family (openai / openai-codex / openai-responses): bare
  ``reasoning_effort`` top-level parameter on Chat Completions for every
  level. The structured ``reasoning: {effort: ...}`` form belongs to
  the Responses API and is rejected by ``client.chat.completions.create()``
  with ``unexpected keyword argument 'reasoning'``. Models that don't
  recognise a value return 400; vtx does not silently swallow that.
- OpenRouter-style gateways (openrouter / kilo / airouter / opencode /
  ollama / tokenrouter, and any openai_compat slug from provider.yaml
  minus the custom-mapped ones): ``reasoning: {effort: ...}`` nested
  object. OpenRouter itself rejects requests that include both
  `reasoning` and `reasoning_effort` with HTTP 400, so the SDK never
  emits the top-level form for these slugs.
- DeepSeek: ``extra_body={"thinking": {"type": ...}}`` + ``reasoning_effort``
  mapped (low/medium -> high, xhigh -> max).
- Zhipu / GLM: ``extra_body={"thinking": {"type": enabled|disabled}}``.
- Anthropic: ``thinking: {type: "enabled", budget_tokens: N}`` for
  non-none; omitted entirely for none.
- Unknown future slug: best-effort OpenRouter-style ``reasoning: {effort: ...}``.
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


def test_openai_minimal_uses_top_level_param() -> None:
    """`reasoning: {effort: ...}` is a Responses-API-only field. Chat
    Completions (which vtx uses via client.chat.completions.create())
    rejects it with `unexpected keyword argument 'reasoning'`. So even
    values like 'minimal' that the Responses API supports must be sent
    as the bare `reasoning_effort` top-level parameter."""
    kwargs = _kwargs("openai", "minimal")
    assert "reasoning" not in kwargs
    assert kwargs.get("reasoning_effort") == "minimal"


def test_openai_xhigh_uses_top_level_param() -> None:
    kwargs = _kwargs("openai", "xhigh")
    assert "reasoning" not in kwargs
    assert kwargs.get("reasoning_effort") == "xhigh"


def test_openai_codex_uses_native_param() -> None:
    assert _kwargs("openai-codex", "high").get("reasoning_effort") == "high"
    assert "reasoning_effort" not in _kwargs("openai-codex", "none")


# --- OpenRouter-style gateways ----------------------------------------------


def test_openrouter_none_sends_exclude() -> None:
    kwargs = _kwargs("openrouter", "none")
    assert kwargs.get("reasoning") == {"effort": "none", "exclude": True}


@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high", "xhigh"])
def test_openrouter_effort_levels(level: str) -> None:
    kwargs = _kwargs("openrouter", level)
    assert kwargs.get("reasoning") == {"effort": level}


@pytest.mark.parametrize("slug", ["kilo", "airouter", "opencode", "ollama"])
@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high", "xhigh"])
def test_openrouter_style_gateways_use_nested_object(slug: str, level: str) -> None:
    """Kilo / ARouter / OpenCode / Ollama all accept the OpenRouter-style
    `reasoning: {effort: ...}` nested form. They must not emit the bare
    `reasoning_effort` top-level parameter, because OpenRouter itself
    rejects requests that include both with HTTP 400."""
    kwargs = _kwargs(slug, level)
    assert "reasoning_effort" not in kwargs
    assert kwargs.get("reasoning") == {"effort": level}


@pytest.mark.parametrize("slug", ["kilo", "airouter", "opencode", "ollama"])
def test_openrouter_style_gateways_none_sends_exclude(slug: str) -> None:
    kwargs = _kwargs(slug, "none")
    assert "reasoning_effort" not in kwargs
    assert kwargs.get("reasoning") == {"effort": "none", "exclude": True}


@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_tokenrouter_effort_levels(level: str) -> None:
    assert _kwargs("tokenrouter", level).get("reasoning") == {"effort": level}


def test_tokenrouter_none_sends_exclude() -> None:
    """TokenRouter uses the OpenRouter-style nested form too; ``none``
    means ``reasoning.effort = "none" + exclude = true``."""
    assert _kwargs("tokenrouter", "none").get("reasoning") == {"effort": "none", "exclude": True}


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


# --- Unknown slug (future gateway) ------------------------------------------


def test_unknown_slug_uses_nested_object() -> None:
    """A new slug we don't recognize should still get a best-effort
    reasoning param via the broadest documented form."""
    assert "reasoning" not in _kwargs(None, "none")
    assert _kwargs(None, "low").get("reasoning") == {"effort": "low"}
    assert _kwargs(None, "high").get("reasoning") == {"effort": "high"}


# --- Auto-detection from provider.yaml -------------------------------------


def test_dynamic_loader_includes_all_openai_compat_gateways() -> None:
    """Every openai_compat slug in provider.yaml (other than the few with
    custom mappings) must be in the dynamic OpenRouter-style set, so a
    new gateway added to provider.yaml is automatically handled
    without code changes here.
    """
    from vtx.llm.provider_catalog import list_providers
    from vtx.llm.sdk.openai import _SLUGS_WITH_CUSTOM_REASONING, _load_openrouter_style_slugs

    openrouter_style = _load_openrouter_style_slugs()

    for p in list_providers():
        if p.family != "openai_compat":
            continue
        if p.slug in _SLUGS_WITH_CUSTOM_REASONING:
            # Custom-mapped slugs (openai, deepseek, zhipu, ...) must
            # NOT be in the OpenRouter-style set.
            assert p.slug not in openrouter_style, (
                f"{p.slug} has a custom mapping but was also added to the OpenRouter-style set"
            )
        else:
            # Every other openai_compat slug MUST be covered.
            assert p.slug in openrouter_style, (
                f"{p.slug} is openai_compat in provider.yaml but missing "
                f"from the OpenRouter-style set"
            )


def test_dynamic_loader_caches_result() -> None:
    """Calling the loader twice must return the same frozenset (cached
    so the catalog is read at most once per process)."""
    from vtx.llm.sdk.openai import _load_openrouter_style_slugs

    a = _load_openrouter_style_slugs()
    b = _load_openrouter_style_slugs()
    assert a is b


@pytest.mark.parametrize(
    "slug",
    [
        "groq",  # native provider serving Llama, also OpenAI-compat
        "together",  # open-source gateway
        "fireworks",
        "mistral",
        "nvidia",
        "deepinfra",
        "huggingface",
        "aihubmix",  # multi-provider gateway
        "apertis",
        "baseten",
        "chutes",
        "cortecs",
        "friendli",
        "knox",
        "llmgateway",
        "modelscope",
        "moonshot",
        "nanogpt",
        "pollinations",
        "routingrun",
        "sherlock",
        "vercelai",
        "zenmux",
        "clarifai",
        "cline",
    ],
)
@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high", "xhigh"])
def test_provider_yaml_gateways_use_nested_object(slug: str, level: str) -> None:
    """Every openai_compat gateway declared in provider.yaml must emit
    the OpenRouter-style nested `reasoning: {effort: ...}` form for
    non-none levels, and the bare `reasoning_effort` top-level field
    must never appear for them.
    """
    kwargs = _kwargs(slug, level)
    assert "reasoning_effort" not in kwargs
    assert kwargs.get("reasoning") == {"effort": level}


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
