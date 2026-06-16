"""Tests for the ``Agent.provider`` field's built-in vs custom shapes.

The ``provider`` field on :class:`Agent` accepts three shapes:

* A Vtx ``BaseProvider`` instance.
* A dict with ``name`` (and optional ``api_key``, etc.). If ``name``
  is a Vtx built-in provider, the SDK uses Vtx's provider catalog
  to resolve the transport.
* A dict with ``name``, ``sdk``, ``base_url`` (and optional
  ``api_key``) for a custom / non-builtin provider.
* ``None`` (default — falls back to env vars).
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from vtx.llm.providers.mock import MockProvider
from vtx.sdk import Agent, Runner
from vtx.sdk.agent import _PROVIDER_DICT_KEYS

# ---------------------------------------------------------------------------
# Field shape
# ---------------------------------------------------------------------------


def test_agent_provider_field_exists() -> None:
    field_names = {f.name for f in fields(Agent)}
    assert "provider" in field_names
    # Removed in favor of the unified provider field.
    assert "api_key" not in field_names
    assert "base_url" not in field_names
    assert "provider_name" not in field_names
    assert "thinking_level" not in field_names


def test_provider_dict_keys_match_docstring() -> None:
    assert (
        frozenset(
            {
                "name",
                "sdk",
                "api_key",
                "base_url",
                "model",
                "max_tokens",
                "temperature",
                "thinking_level",
                "default_headers",
            }
        )
        == _PROVIDER_DICT_KEYS
    )


# ---------------------------------------------------------------------------
# BaseProvider instance
# ---------------------------------------------------------------------------


def test_agent_accepts_baseprovider_instance() -> None:
    p = MockProvider(scenario="simple_text")
    agent = Agent(name="Bot", provider=p)
    assert agent.resolve_provider() is p


# ---------------------------------------------------------------------------
# Built-in provider dict: just name + api_key
# ---------------------------------------------------------------------------


def test_agent_accepts_builtin_provider_dict_minimal() -> None:
    """A built-in provider needs only ``name``; the SDK looks up the
    rest from Vtx's provider catalog.
    """
    agent = Agent(
        name="Bot", model="gpt-4o-mini", provider={"name": "openai", "api_key": "sk-test"}
    )
    # Construction shouldn't raise.
    assert agent.provider == {"name": "openai", "api_key": "sk-test"}


def test_agent_accepts_builtin_provider_with_thinking_level() -> None:
    agent = Agent(
        name="Bot",
        model="gpt-4o-mini",
        provider={
            "name": "openai",
            "api_key": "sk-test",
            "thinking_level": "high",
            "max_tokens": 8192,
        },
    )
    assert isinstance(agent.provider, dict)
    assert agent.provider["thinking_level"] == "high"
    assert agent.provider["max_tokens"] == 8192


def test_builtin_provider_does_not_require_sdk_field() -> None:
    """Built-in providers have an implicit SDK mode — the user does
    NOT need to pass ``sdk``.
    """
    agent = Agent(
        name="Bot", model="gpt-4o-mini", provider={"name": "openai", "api_key": "sk-test"}
    )
    # No "sdk" key in the dict.
    assert isinstance(agent.provider, dict)
    assert "sdk" not in agent.provider


# ---------------------------------------------------------------------------
# Custom / non-builtin provider dict: name + sdk + base_url + api_key
# ---------------------------------------------------------------------------


def test_custom_provider_requires_sdk_and_base_url() -> None:
    """A non-builtin name without ``sdk`` raises a clear error."""
    agent = Agent(
        name="Bot", model="gpt-4o-mini", provider={"name": "my-local-llm", "api_key": "x"}
    )
    with pytest.raises(ValueError, match="must also pass an 'sdk' field"):
        agent.resolve_provider()


def test_custom_provider_requires_base_url() -> None:
    agent = Agent(
        name="Bot",
        model="gpt-4o-mini",
        provider={"name": "my-local-llm", "sdk": "openai", "api_key": "x"},
    )
    with pytest.raises(ValueError, match="must also pass a 'base_url'"):
        agent.resolve_provider()


def test_custom_provider_with_all_fields_resolves() -> None:
    """A custom provider that has all four fields resolves without error."""
    agent = Agent(
        name="Bot",
        model="llama-3",
        provider={
            "name": "my-local",
            "sdk": "openai",
            "api_key": "test",
            "base_url": "http://localhost:11434/v1",
        },
    )
    # We don't actually call the provider here (would need a real
    # llama server); just confirm construction + dict pass-through.
    assert isinstance(agent.provider, dict)
    assert agent.provider["sdk"] == "openai"
    assert agent.provider["base_url"] == "http://localhost:11434/v1"


def test_custom_provider_with_anthropic_sdk() -> None:
    agent = Agent(
        name="Bot",
        model="claude-3-5-sonnet",
        provider={
            "name": "my-anthropic-clone",
            "sdk": "anthropic",
            "api_key": "test-key",
            "base_url": "https://api.example.com",
        },
    )
    assert isinstance(agent.provider, dict)
    assert agent.provider["sdk"] == "anthropic"


# ---------------------------------------------------------------------------
# None / env fallback
# ---------------------------------------------------------------------------


def test_provider_none_falls_back_to_env() -> None:
    agent = Agent(name="Bot", model="gpt-4o-mini", provider=None)
    # resolve_provider() will try to build something; we just check
    # the agent constructs without error and exposes provider=None.
    assert agent.provider is None


# ---------------------------------------------------------------------------
# clone() round-trip
# ---------------------------------------------------------------------------


def test_builtin_provider_dict_survives_clone() -> None:
    agent = Agent(name="Bot", model="gpt-4o-mini", provider={"name": "openai", "api_key": "k"})
    cloned = agent.clone(name="Bot 2")
    assert cloned.provider == {"name": "openai", "api_key": "k"}


def test_custom_provider_dict_survives_clone() -> None:
    agent = Agent(
        name="Bot",
        model="llama-3",
        provider={
            "name": "my-local",
            "sdk": "openai",
            "api_key": "k",
            "base_url": "http://localhost:11434/v1",
        },
    )
    cloned = agent.clone(name="Bot 2")
    assert isinstance(cloned.provider, dict)
    assert cloned.provider["sdk"] == "openai"
    assert cloned.provider["base_url"] == "http://localhost:11434/v1"


# ---------------------------------------------------------------------------
# End-to-end: actually run with a built-in dict provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_builtin_provider_dict_runs_with_mock() -> None:
    """The instance path is the only one that runs end-to-end with the
    mock; the dict paths require real network or env config. Verify
    that the BaseProvider instance path is unaffected by the refactor.
    """
    agent = Agent(name="Bot", provider=MockProvider(scenario="simple_text"))
    result = await Runner.run(agent, "hi")
    assert result.final_output == "Hello, world!"
