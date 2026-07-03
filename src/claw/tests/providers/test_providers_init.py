"""Tests for lazy provider exports from vtx_claw.providers."""

from __future__ import annotations

import importlib
import sys


def test_importing_providers_package_is_lazy(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "vtx_claw.providers", raising=False)
    monkeypatch.delitem(sys.modules, "vtx_claw.providers.anthropic_provider", raising=False)
    monkeypatch.delitem(sys.modules, "vtx_claw.providers.openai_compat_provider", raising=False)
    monkeypatch.delitem(sys.modules, "vtx_claw.providers.openai_codex_provider", raising=False)
    monkeypatch.delitem(sys.modules, "vtx_claw.providers.github_copilot_provider", raising=False)
    monkeypatch.delitem(sys.modules, "vtx_claw.providers.azure_openai_provider", raising=False)
    monkeypatch.delitem(sys.modules, "vtx_claw.providers.bedrock_provider", raising=False)

    providers = importlib.import_module("vtx_claw.providers")

    assert "vtx_claw.providers.anthropic_provider" not in sys.modules
    assert "vtx_claw.providers.openai_compat_provider" not in sys.modules
    assert "vtx_claw.providers.openai_codex_provider" not in sys.modules
    assert "vtx_claw.providers.github_copilot_provider" not in sys.modules
    assert "vtx_claw.providers.azure_openai_provider" not in sys.modules
    assert "vtx_claw.providers.bedrock_provider" not in sys.modules
    assert providers.__all__ == [
        "LLMProvider",
        "LLMResponse",
        "AnthropicProvider",
        "OpenAICompatProvider",
        "OpenAICodexProvider",
        "GitHubCopilotProvider",
        "AzureOpenAIProvider",
        "BedrockProvider",
    ]


def test_explicit_provider_import_still_works(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "vtx_claw.providers", raising=False)
    monkeypatch.delitem(sys.modules, "vtx_claw.providers.anthropic_provider", raising=False)

    namespace: dict[str, object] = {}
    exec("from vtx_claw.providers import AnthropicProvider", namespace)

    assert namespace["AnthropicProvider"].__name__ == "AnthropicProvider"
    assert "vtx_claw.providers.anthropic_provider" in sys.modules


def test_openai_codex_supports_progress_deltas() -> None:
    from vtx_claw.providers.openai_codex_provider import OpenAICodexProvider

    assert OpenAICodexProvider.supports_progress_deltas is True
