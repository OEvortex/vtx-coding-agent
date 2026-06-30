"""LLM provider abstraction module."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from vtx_claw.providers.base import LLMProvider, LLMResponse

__all__ = [
    "AnthropicProvider",
    "AzureOpenAIProvider",
    "BedrockProvider",
    "GitHubCopilotProvider",
    "LLMProvider",
    "LLMResponse",
    "OpenAICodexProvider",
    "OpenAICompatProvider",
]

_LAZY_IMPORTS = {
    "AnthropicProvider": ".anthropic_provider",
    "OpenAICompatProvider": ".openai_compat_provider",
    "OpenAICodexProvider": ".openai_codex_provider",
    "GitHubCopilotProvider": ".github_copilot_provider",
    "AzureOpenAIProvider": ".azure_openai_provider",
    "BedrockProvider": ".bedrock_provider",
}

if TYPE_CHECKING:
    from vtx_claw.providers.anthropic_provider import AnthropicProvider
    from vtx_claw.providers.azure_openai_provider import AzureOpenAIProvider
    from vtx_claw.providers.bedrock_provider import BedrockProvider
    from vtx_claw.providers.github_copilot_provider import GitHubCopilotProvider
    from vtx_claw.providers.openai_codex_provider import OpenAICodexProvider
    from vtx_claw.providers.openai_compat_provider import OpenAICompatProvider


def __getattr__(name: str):
    """Lazily expose provider implementations without importing all backends up front."""
    module_name = _LAZY_IMPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    return getattr(module, name)
