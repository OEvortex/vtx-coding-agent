"""Model information helpers for the onboard wizard.

Provides popular model suggestions and context-window limits for the
interactive onboarding autocomplete and auto-fill features.
"""

from __future__ import annotations

from typing import Any

# Popular models per provider (name -> context window tokens)
_POPULAR_MODELS: dict[str, dict[str, int]] = {
    "openai": {
        "gpt-4o": 128_000,
        "gpt-4o-mini": 128_000,
        "gpt-4.1": 1_048_576,
        "gpt-4.1-mini": 1_048_576,
        "gpt-4.1-nano": 1_048_576,
        "o1": 200_000,
        "o1-mini": 128_000,
        "o1-pro": 200_000,
        "o3": 200_000,
        "o3-mini": 200_000,
        "o4-mini": 200_000,
    },
    "anthropic": {
        "claude-sonnet-4-20250514": 200_000,
        "claude-3-5-haiku-20241022": 200_000,
        "claude-3-5-sonnet-20241022": 200_000,
        "claude-3-opus-20240229": 200_000,
    },
    "deepseek": {"deepseek-chat": 131_072, "deepseek-reasoner": 131_072},
    "google": {
        "gemini-2.5-pro": 1_048_576,
        "gemini-2.5-flash": 1_048_576,
        "gemini-2.0-flash": 1_048_576,
        "gemini-1.5-pro": 2_097_152,
        "gemini-1.5-flash": 1_048_576,
    },
    "groq": {
        "llama-3.3-70b-versatile": 128_000,
        "llama-3.1-8b-instant": 128_000,
        "mixtral-8x7b-32768": 32_768,
        "gemma2-9b-it": 8_192,
    },
    "openrouter": {
        "openai/gpt-4o": 128_000,
        "anthropic/claude-sonnet-4-20250514": 200_000,
        "deepseek/deepseek-chat": 131_072,
        "google/gemini-2.5-pro": 1_048_576,
        "meta-llama/llama-3.3-70b-instruct": 128_000,
    },
    "dashscope": {
        "qwen-max": 32_768,
        "qwen-plus": 131_072,
        "qwen-turbo": 131_072,
        "qwen-long": 10_000_000,
        "qwen-vl-max": 32_768,
    },
    "zhipu": {
        "glm-4-plus": 128_000,
        "glm-4-flash": 128_000,
        "glm-4-long": 1_000_000,
        "glm-4v-plus": 8_192,
        "codegeex-4": 128_000,
    },
    "volcengine": {
        "doubao-1.5-pro-256k": 262_144,
        "doubao-1.5-lite-32k": 32_768,
        "deepseek-v3-250324": 131_072,
        "deepseek-r1-250528": 131_072,
    },
    "xiaomi_mimo": {"MiMo-72B-A17B": 131_072},
    "minimax": {"MiniMax-Text-01": 4_000_000, "abab6.5s-chat": 1_000_000},
    "stepfun": {"step-2-16k": 16_384, "step-1-32k": 32_768},
    "ollama": {
        "llama3.1": 128_000,
        "llama3.2": 128_000,
        "qwen2.5": 131_072,
        "mistral": 32_768,
        "codellama": 16_384,
        "gemma2": 131_072,
    },
    "vllm": {},
}

# Flat lookup: model_id -> context_tokens (across all providers)
_ALL_CONTEXT_LIMITS: dict[str, int] = {}
for _models in _POPULAR_MODELS.values():
    _ALL_CONTEXT_LIMITS.update(_models)


def get_all_models() -> list[str]:
    """Return all known model IDs across providers."""
    seen: set[str] = set()
    result: list[str] = []
    for models in _POPULAR_MODELS.values():
        for model_id in models:
            if model_id not in seen:
                seen.add(model_id)
                result.append(model_id)
    return sorted(result)


def find_model_info(model_name: str) -> dict[str, Any] | None:
    """Look up basic info for a model. Returns None if unknown."""
    ctx = _ALL_CONTEXT_LIMITS.get(model_name)
    if ctx is None:
        return None
    return {"model": model_name, "context_window_tokens": ctx}


def get_model_context_limit(model: str, provider: str = "auto") -> int | None:
    """Return the context-window token limit for a model, or None if unknown."""
    if provider and provider != "auto" and provider in _POPULAR_MODELS:
        ctx = _POPULAR_MODELS[provider].get(model)
        if ctx is not None:
            return ctx
    return _ALL_CONTEXT_LIMITS.get(model)


def get_model_suggestions(partial: str, provider: str = "auto", limit: int = 20) -> list[str]:
    """Return model ID suggestions matching ``partial`` for a given provider."""
    partial_lower = partial.lower()
    candidates: list[str] = []

    if provider and provider != "auto" and provider in _POPULAR_MODELS:
        for model_id in _POPULAR_MODELS[provider]:
            if partial_lower in model_id.lower():
                candidates.append(model_id)

    if not candidates or len(candidates) < limit:
        for models in _POPULAR_MODELS.values():
            for model_id in models:
                if model_id not in candidates and partial_lower in model_id.lower():
                    candidates.append(model_id)

    return candidates[:limit]


def format_token_count(tokens: int) -> str:
    """Format token count for display (e.g., 200000 -> '200,000')."""
    return f"{tokens:,}"
