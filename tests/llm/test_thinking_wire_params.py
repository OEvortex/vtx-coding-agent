"""Tests for the thinking/reasoning wire parameters.

The OpenAI SDK and Anthropic SDK translate ``GenerationConfig.thinking_level``
into request fields. The dispatch is whitelisted by slug:

- **OpenAI family (openai-codex / openai-responses)**: bare top-level
  ``reasoning_effort`` parameter. The openai Python SDK's
  ``client.chat.completions.create()`` only accepts this — the
  structured ``reasoning: {effort: ...}`` form is Responses-API-only
  and is rejected with
  ``unexpected keyword argument 'reasoning'``. ``"none"`` is implemented
  as omission (no documented Chat-Completions off switch for o-series /
  gpt-5; the model picks its default).

- **Every other openai_compat provider** (openai, openrouter, kilo,
  tokenrouter, deepseek, zhipu, etc.): no thinking-related wire
  parameter is emitted at any level. The picker still works (the user
  can still select a level) but the level is not sent to the wire —
  the model uses its own default.

- **Anthropic**: ``thinking: {type: "enabled", budget_tokens: N}`` for
  non-none levels; omitted entirely for none.

Reference:
  https://github.com/openai/openai-python/blob/main/src/openai/resources/chat/completions/completions.py
  https://platform.claude.com/docs/en/build-with-claude/extended-thinking
"""

from __future__ import annotations

import pytest

from vtx.llm.sdk.anthropic import AnthropicSDK
from vtx.llm.sdk.base import GenerationConfig, Message
from vtx.llm.sdk.openai import OpenAISDK

_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh")
_NON_NONE_LEVELS = tuple(level for level in _LEVELS if level != "none")

_THINKING_ENABLED_SLUGS = ("openai-codex", "openai-responses")
_DISABLED_SLUGS = (
    "openai",
    "openrouter",
    "kilo",
    "tokenrouter",
    "deepseek",
    "zhipu",
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
)


def _kwargs(slug: str, level: str | None) -> dict:
    sdk = OpenAISDK(api_key="x", base_url="https://example.com", provider_slug=slug)
    msgs = [Message(role="user", content="hi")]
    cfg = GenerationConfig(model="m", thinking_level=level)
    return sdk._build_kwargs(msgs, cfg)


def _payload(level: str | None) -> dict:
    sdk = AnthropicSDK(api_key="x", base_url="https://example.com")
    msgs = [Message(role="user", content="hi")]
    cfg = GenerationConfig(model="m", thinking_level=level)
    return sdk._build_payload(msgs, cfg)


# --- OpenAI family (whitelisted: bare reasoning_effort) ---------------------


@pytest.mark.parametrize("slug", _THINKING_ENABLED_SLUGS)
@pytest.mark.parametrize("level", _NON_NONE_LEVELS)
def test_openai_whitelisted_emits_bare_reasoning_effort(slug: str, level: str) -> None:
    kwargs = _kwargs(slug, level)
    assert kwargs.get("reasoning_effort") == level
    assert "reasoning" not in kwargs
    assert "extra_body" not in kwargs


@pytest.mark.parametrize("slug", _THINKING_ENABLED_SLUGS)
def test_openai_whitelisted_none_omits_param(slug: str) -> None:
    kwargs = _kwargs(slug, "none")
    assert "reasoning_effort" not in kwargs
    assert "reasoning" not in kwargs
    assert "extra_body" not in kwargs


def test_openai_whitelisted_no_level_omits_param() -> None:
    for slug in _THINKING_ENABLED_SLUGS:
        kwargs = _kwargs(slug, None)
        assert "reasoning_effort" not in kwargs
        assert "reasoning" not in kwargs
        assert "extra_body" not in kwargs


# --- Other openai_compat providers (whitelist off: no thinking wire) -------


@pytest.mark.parametrize("slug", _DISABLED_SLUGS)
@pytest.mark.parametrize("level", _LEVELS)
def test_other_openai_compat_emits_no_thinking_param(slug: str, level: str) -> None:
    """For every openai_compat slug not in the whitelist, the
    thinking-level picker state is preserved but no thinking-related
    wire parameter is sent. The model uses its own default.
    """
    kwargs = _kwargs(slug, level)
    assert "reasoning_effort" not in kwargs, (
        f"{slug!r} at level={level!r} emitted reasoning_effort; "
        f"the dispatch is whitelisted to {list(_THINKING_ENABLED_SLUGS)} only."
    )
    assert "reasoning" not in kwargs, (
        f"{slug!r} at level={level!r} emitted the 'reasoning' kwarg; "
        f"client.chat.completions.create() rejects it with "
        f"'unexpected keyword argument \\'reasoning\\''."
    )
    assert "extra_body" not in kwargs


def test_minimax_m3_does_not_emit_thinking_extra_body() -> None:
    """Regression test for the user's report: MiniMax M3 on tokenrouter
    was being sent ``extra_body={'thinking': ...}`` which the openai
    SDK's body builder handles, but the model still ignored the off
    signal. Per the user's instruction, we now drop the dispatch
    entirely for non-whitelisted slugs — the picker still shows the
    selected level but the wire gets nothing.
    """
    for level in _LEVELS:
        kwargs = _kwargs("tokenrouter", level)
        assert "extra_body" not in kwargs
        assert "reasoning_effort" not in kwargs
        assert "reasoning" not in kwargs


# --- catalog coverage ------------------------------------------------------


def test_every_openai_compat_slug_in_catalog_is_classified() -> None:
    """Every openai_compat slug in ``provider.yaml`` is either in the
    whitelist (emits ``reasoning_effort``) or in the disabled set
    (emits nothing). No third path exists.
    """
    from vtx.llm.provider_catalog import list_providers

    catalog_slugs = {p.slug for p in list_providers() if p.family == "openai_compat"}
    assert catalog_slugs, "provider.yaml must declare at least one openai_compat slug"

    for slug in catalog_slugs:
        whitelisted = slug in _THINKING_ENABLED_SLUGS
        # We don't assert against _DISABLED_SLUGS — the catalog is the
        # source of truth. The whitelist is hard-coded; every other
        # openai_compat slug falls into the disabled bucket.
        kwargs = _kwargs(slug, "high")
        if whitelisted:
            assert kwargs.get("reasoning_effort") == "high", (
                f"{slug!r} is whitelisted but did not emit reasoning_effort"
            )
        else:
            assert "reasoning_effort" not in kwargs, (
                f"{slug!r} is not whitelisted but emitted reasoning_effort"
            )


# --- Anthropic -------------------------------------------------------------


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
