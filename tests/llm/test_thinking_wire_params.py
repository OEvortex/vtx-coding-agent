"""Tests for the thinking/reasoning wire parameters.

Every ``openai_compat`` provider in ``provider.yaml`` speaks the same Chat
Completions wire, so the OpenAI SDK and Anthropic SDK translate
``GenerationConfig.thinking_level`` into request fields as follows:

- **OpenAI Chat Completions**: bare top-level ``reasoning_effort``. This is a
  standard field, so it is sent for *every* ``openai_compat`` provider — an
  earlier slug allow-list meant the level was silently dropped on all but
  three providers, which is indistinguishable from the level not working.
  Only the standard field is ever used: a previous attempt sent
  ``extra_body={'thinking': ...}``, a non-standard shape that some gateways
  accepted while the model ignored it, and that shape stays banned (see
  ``test_minimax_m3_does_not_emit_thinking_extra_body``).

  ``"none"`` is sent as the literal ``reasoning_effort: "none"`` whenever the
  catalog verified the model takes a ``none`` effort (models.dev publishes
  ``values: ["none", "low", ...]``). Omitting the field instead leaves the
  model on its own default, which for a reasoning model means *still
  thinking* — so omission is only correct when no ``none`` effort is verified.

- **Anthropic**: ``thinking: {type: "enabled", budget_tokens: N}`` for
  non-none levels; omitted entirely for none (Anthropic's documented off
  switch is simply not sending ``thinking``).

Reference:
  https://github.com/openai/openai-python/blob/main/src/openai/resources/chat/completions/completions.py
  https://platform.claude.com/docs/en/build-with-claude/extended-thinking
"""

from __future__ import annotations

import pytest

from vtx.ai.sdk.anthropic import AnthropicSDK
from vtx.ai.sdk.base import GenerationConfig, Message
from vtx.ai.sdk.openai import OpenAISDK

_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh")
_NON_NONE_LEVELS = tuple(level for level in _LEVELS if level != "none")

# A representative slice of openai_compat slugs, including the ones that used
# to be blocked by the allow-list.
_SLUGS = (
    "openai",
    "openai-codex",
    "openai-responses",
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

# gpt-5.1 publishes exactly these efforts, including an explicit "none".
_NONE_CAPABLE_MAP: dict[str, str | None] = {
    "off": "none",
    "minimal": None,
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": None,
    "max": None,
}


def _kwargs(slug: str, level: str | None) -> dict:
    sdk = OpenAISDK(api_key="x", base_url="https://example.com", provider_slug=slug)
    msgs = [Message(role="user", content="hi")]
    cfg = GenerationConfig(model="m", thinking_level=level)
    return sdk._build_kwargs(msgs, cfg)


def _kwargs_with_map(slug: str, level: str, level_map: dict[str, str | None]) -> dict:
    sdk = OpenAISDK(api_key="x", base_url="https://example.com", provider_slug=slug)
    return sdk._build_kwargs(
        [Message(role="user", content="hi")],
        GenerationConfig(model="m", thinking_level=level, thinking_level_map=level_map),
    )


def _payload(level: str | None) -> dict:
    sdk = AnthropicSDK(api_key="x", base_url="https://example.com")
    msgs = [Message(role="user", content="hi")]
    cfg = GenerationConfig(model="m", thinking_level=level)
    return sdk._build_payload(msgs, cfg)


# --- OpenAI Chat Completions: the level always reaches the wire --------------


@pytest.mark.parametrize("slug", _SLUGS)
@pytest.mark.parametrize("level", _NON_NONE_LEVELS)
def test_level_reaches_wire_on_every_openai_compat_provider(slug: str, level: str) -> None:
    kwargs = _kwargs(slug, level)
    assert kwargs.get("reasoning_effort") == level
    assert "reasoning" not in kwargs
    assert "extra_body" not in kwargs


@pytest.mark.parametrize("slug", _SLUGS)
def test_verified_none_effort_is_sent_rather_than_omitted(slug: str) -> None:
    """The reported bug: "thinking off" left the model thinking.

    A catalog-verified ``none`` effort is the only documented way to stop
    reasoning, so it must reach the wire instead of the field being dropped.
    """
    kwargs = _kwargs_with_map(slug, "none", _NONE_CAPABLE_MAP)
    assert kwargs.get("reasoning_effort") == "none"


@pytest.mark.parametrize("slug", _SLUGS)
def test_none_without_verified_effort_omits_the_field(slug: str) -> None:
    """With no catalog evidence of a ``none`` effort, omission is correct —
    the model picks its own default, which is the only safe choice."""
    kwargs = _kwargs_with_map(slug, "none", {"off": None, "low": "low"})
    assert "reasoning_effort" not in kwargs
    assert "reasoning" not in kwargs


def test_off_spelling_is_equivalent_to_none() -> None:
    """``off`` (catalog spelling) and ``none`` (provider spelling) are the
    same level and must produce the same wire params."""
    assert _kwargs_with_map("openai", "off", _NONE_CAPABLE_MAP) == _kwargs_with_map(
        "openai", "none", _NONE_CAPABLE_MAP
    )


def test_default_level_sends_nothing() -> None:
    """``default`` means "let the model decide" and must stay silent."""
    kwargs = _kwargs_with_map("openai", "default", _NONE_CAPABLE_MAP)
    assert "reasoning_effort" not in kwargs
    assert "reasoning" not in kwargs


def test_no_level_omits_param() -> None:
    for slug in _SLUGS:
        kwargs = _kwargs(slug, None)
        assert "reasoning_effort" not in kwargs
        assert "reasoning" not in kwargs
        assert "extra_body" not in kwargs


def test_explicitly_unsupported_level_is_omitted() -> None:
    """A level the catalog marks unsupported (``None``) is never sent, even
    when the level itself is otherwise in the model's vocab."""
    kwargs = _kwargs_with_map("openai", "xhigh", _NONE_CAPABLE_MAP)
    assert "reasoning_effort" not in kwargs


def test_minimax_m3_does_not_emit_thinking_extra_body() -> None:
    """Regression test for the user's original report: MiniMax M3 on
    tokenrouter was sent ``extra_body={'thinking': ...}``, a non-standard
    shape the SDK's body builder accepted while the model ignored it. Only
    the standard ``reasoning_effort`` field may be used.
    """
    for level in _LEVELS:
        kwargs = _kwargs("tokenrouter", level)
        assert "extra_body" not in kwargs
        assert "reasoning" not in kwargs


def test_every_openai_compat_slug_in_catalog_sends_the_level() -> None:
    """Every ``openai_compat`` slug in ``provider.yaml`` must forward a
    verified level on the wire — none may silently swallow it."""
    from vtx.ai.provider_catalog import list_providers

    catalog_slugs = {p.slug for p in list_providers() if p.family == "openai_compat"}
    assert catalog_slugs, "provider.yaml must declare at least one openai_compat slug"

    for slug in sorted(catalog_slugs):
        kwargs = _kwargs_with_map(slug, "high", _NONE_CAPABLE_MAP)
        assert kwargs.get("reasoning_effort") == "high", (
            f"{slug!r} did not emit reasoning_effort for a verified level; "
            "the thinking level would be a no-op on this provider."
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


# --- catalog-verified maps -------------------------------------------------


def test_verified_max_tier_reaches_chat_completions() -> None:
    """gpt-5.6 advertises a ``max`` effort; the level has to survive the
    catalog map onto the wire instead of being dropped."""
    kwargs = _kwargs_with_map(
        "openai",
        "max",
        {"off": "none", "low": "low", "high": "high", "xhigh": "xhigh", "max": "max"},
    )
    assert kwargs.get("reasoning_effort") == "max"


def test_budget_style_map_sends_budget_tokens_on_anthropic() -> None:
    """Claude Haiku/Sonnet 4.5 publish a token budget, not named efforts."""
    sdk = AnthropicSDK(api_key="x", base_url="https://example.com")
    payload = sdk._build_payload(
        [Message(role="user", content="hi")],
        GenerationConfig(
            model="m",
            thinking_level="high",
            thinking_level_map={"off": "none", "low": "budget:2048", "high": "budget:8192"},
        ),
    )
    assert payload.get("thinking") == {"type": "enabled", "budget_tokens": 8192}


def test_budget_style_map_never_reaches_other_transports() -> None:
    """A token budget has no spelling outside the Anthropic Messages API, so
    it must be dropped rather than forwarded as a bogus effort."""
    kwargs = _kwargs_with_map("openai", "high", {"off": "none", "high": "budget:8192"})
    assert "reasoning_effort" not in kwargs
    assert "reasoning" not in kwargs


def test_budget_style_map_respects_max_tokens() -> None:
    sdk = AnthropicSDK(api_key="x", base_url="https://example.com")
    payload = sdk._build_payload(
        [Message(role="user", content="hi")],
        GenerationConfig(
            model="m",
            thinking_level="high",
            max_tokens=4096,
            thinking_level_map={"off": "none", "high": "budget:8192"},
        ),
    )
    assert payload["thinking"]["budget_tokens"] == 4096 - 1024
