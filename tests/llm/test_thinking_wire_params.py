"""Tests for the per-provider thinking/reasoning wire parameters.

The OpenAI SDK and Anthropic SDK translate ``GenerationConfig.thinking_level``
into the specific request fields documented for each provider family:

- **OpenRouter family** (openrouter / kilo / tokenrouter): nested
  ``reasoning: {effort: ..., exclude: True}`` object. OpenRouter itself
  rejects requests that include both ``reasoning`` and
  ``reasoning_effort`` with HTTP 400.
- **DeepSeek**: ``extra_body={"thinking": {"type": ...}}`` + mapped
  ``reasoning_effort`` (low/medium -> high, xhigh -> max).
- **Zhipu / GLM**: ``extra_body={"thinking": {"type": enabled|disabled}}``
  on/off toggle.
- **Every other openai_compat provider** (auto-detected from
  provider.yaml, with a static baseline): bare ``reasoning_effort``
  top-level parameter. This is the OpenAI Chat Completions wire format
  that the openai Python SDK accepts.
- **Anthropic**: ``thinking: {type: "enabled", budget_tokens: N}`` for
  non-none levels; omitted entirely for none.

Reference:
  https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/provider/transform.ts
  https://github.com/openai/openai-python/blob/main/src/openai/resources/chat/completions/completions.py
  https://platform.claude.com/docs/en/build-with-claude/extended-thinking
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


# --- OpenRouter family (nested reasoning object) ---------------------------


def test_openrouter_none_sends_exclude() -> None:
    kwargs = _kwargs("openrouter", "none")
    assert kwargs.get("reasoning") == {"effort": "none", "exclude": True}
    assert "reasoning_effort" not in kwargs


@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high", "xhigh"])
def test_openrouter_effort_levels(level: str) -> None:
    kwargs = _kwargs("openrouter", level)
    assert kwargs.get("reasoning") == {"effort": level}
    assert "reasoning_effort" not in kwargs


@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high", "xhigh"])
def test_kilo_uses_openrouter_style(level: str) -> None:
    """Kilo is documented as OpenRouter-compatible."""
    kwargs = _kwargs("kilo", level)
    assert "reasoning_effort" not in kwargs
    assert kwargs.get("reasoning") == {"effort": level}


def test_kilo_none_sends_exclude() -> None:
    kwargs = _kwargs("kilo", "none")
    assert kwargs.get("reasoning") == {"effort": "none", "exclude": True}


@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high", "xhigh"])
def test_tokenrouter_uses_openrouter_style(level: str) -> None:
    """TokenRouter uses the Responses API style nested object."""
    kwargs = _kwargs("tokenrouter", level)
    assert "reasoning_effort" not in kwargs
    assert kwargs.get("reasoning") == {"effort": level}


def test_tokenrouter_none_sends_exclude() -> None:
    kwargs = _kwargs("tokenrouter", "none")
    assert kwargs.get("reasoning") == {"effort": "none", "exclude": True}


# --- OpenAI Chat Completions (bare reasoning_effort) -------------------------


@pytest.mark.parametrize("slug", ["openai", "openai-codex", "openai-responses"])
def test_openai_family_uses_top_level_param(slug: str) -> None:
    for level in ("low", "medium", "high"):
        kwargs = _kwargs(slug, level)
        assert kwargs.get("reasoning_effort") == level
        assert "reasoning" not in kwargs


@pytest.mark.parametrize("slug", ["openai", "openai-codex", "openai-responses"])
def test_openai_family_minimal_xhigh_also_uses_top_level(slug: str) -> None:
    """The Responses API structured form (reasoning: {effort: ...}) is
    rejected by client.chat.completions.create(). Even minimal and
    xhigh levels must use the bare top-level parameter."""
    for level in ("minimal", "xhigh"):
        kwargs = _kwargs(slug, level)
        assert "reasoning" not in kwargs
        assert kwargs.get("reasoning_effort") == level


def test_openai_none_omits_reasoning() -> None:
    """Chat Completions has no documented off switch for o-series /
    gpt-5: omitting the parameter lets the model pick its default."""
    for slug in ("openai", "openai-codex", "openai-responses"):
        kwargs = _kwargs(slug, "none")
        assert "reasoning_effort" not in kwargs
        assert "reasoning" not in kwargs


@pytest.mark.parametrize(
    "slug",
    [
        # Native + gateway providers from the static baseline.
        "groq",
        "together",
        "fireworks",
        "mistral",
        "nvidia",
        "deepinfra",
        "huggingface",
        "airouter",
        "opencode",
        "ollama",
    ],
)
@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high", "xhigh"])
def test_chat_completions_gateways_use_top_level_param(slug: str, level: str) -> None:
    """Every native / openai_compat gateway in the static baseline
    uses the bare ``reasoning_effort`` top-level parameter, matching
    opencode's behavior for ``@ai-sdk/openai-compatible``."""
    kwargs = _kwargs(slug, level)
    assert "reasoning" not in kwargs
    assert kwargs.get("reasoning_effort") == level


@pytest.mark.parametrize(
    "slug",
    [
        "groq",
        "together",
        "fireworks",
        "mistral",
        "nvidia",
        "deepinfra",
        "huggingface",
        "airouter",
        "opencode",
        "ollama",
    ],
)
def test_chat_completions_gateways_none_omits(slug: str) -> None:
    kwargs = _kwargs(slug, "none")
    assert "reasoning_effort" not in kwargs
    assert "reasoning" not in kwargs


# --- provider.yaml auto-detection (Chat Completions) ------------------------


def test_dynamic_loader_includes_all_openai_compat_gateways() -> None:
    """Every openai_compat slug in provider.yaml (other than the few
    with custom mappings) must be in the dynamic Chat-Completions
    set, so a new gateway added to provider.yaml is automatically
    handled without code changes.
    """
    from vtx.llm.provider_catalog import list_providers
    from vtx.llm.sdk.openai import (
        _SLUGS_WITH_EXTRA_BODY_THINKING,
        _SLUGS_WITH_REASONING_OBJECT,
        _load_chat_completions_slugs,
    )

    chat_completions = _load_chat_completions_slugs()

    for p in list_providers():
        if p.family != "openai_compat":
            continue
        excluded = (
            p.slug in _SLUGS_WITH_REASONING_OBJECT or p.slug in _SLUGS_WITH_EXTRA_BODY_THINKING
        )
        if excluded:
            # Custom-mapped slugs (openrouter, kilo, tokenrouter,
            # deepseek, zhipu) must NOT be in the Chat-Completions
            # set.
            assert p.slug not in chat_completions, (
                f"{p.slug} has a custom mapping but was also added to the Chat-Completions set"
            )
        else:
            # Every other openai_compat slug MUST be covered.
            assert p.slug in chat_completions, (
                f"{p.slug} is openai_compat in provider.yaml but missing "
                f"from the Chat-Completions set"
            )


def test_dynamic_loader_caches_result() -> None:
    from vtx.llm.sdk.openai import _load_chat_completions_slugs

    a = _load_chat_completions_slugs()
    b = _load_chat_completions_slugs()
    assert a is b


@pytest.mark.parametrize(
    "slug",
    [
        "aihubmix",
        "apertis",
        "baseten",
        "berget",
        "blackbox",
        "chutes",
        "cortecs",
        "crof",
        "dialagram",
        "dinference",
        "friendli",
        "hicapai",
        "jiekou",
        "knox",
        "lightningai",
        "llmgateway",
        "meganova",
        "moark",
        "modelscope",
        "moonshot",
        "nanogpt",
        "pollinations",
        "routingrun",
        "seraphyn",
        "sherlock",
        "vercelai",
        "zenmux",
        "clarifai",
        "cline",
    ],
)
@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high", "xhigh"])
def test_provider_yaml_gateways_use_bare_param(slug: str, level: str) -> None:
    """Every openai_compat gateway declared in provider.yaml must
    emit the bare ``reasoning_effort`` top-level parameter (the
    OpenAI Chat Completions wire format), not the nested
    ``reasoning: {effort: ...}`` form reserved for OpenRouter.
    """
    kwargs = _kwargs(slug, level)
    assert "reasoning" not in kwargs
    assert kwargs.get("reasoning_effort") == level


# --- DeepSeek (extra_body + reasoning_effort) ------------------------------


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


# --- Zhipu / GLM (on/off toggle) -------------------------------------------


def test_zhipu_none_disables_thinking() -> None:
    kwargs = _kwargs("zhipu", "none")
    assert kwargs.get("extra_body") == {"thinking": {"type": "disabled"}}


@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high", "xhigh"])
def test_zhipu_enables_thinking_for_any_non_none_level(level: str) -> None:
    kwargs = _kwargs("zhipu", level)
    assert kwargs.get("extra_body") == {"thinking": {"type": "enabled"}}


# --- Anthropic --------------------------------------------------------------


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
