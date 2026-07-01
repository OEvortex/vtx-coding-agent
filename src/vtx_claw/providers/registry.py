"""
Provider Registry — wraps vtx's provider catalog as the single source of truth.

All provider metadata (slug, family, api_key_env, base_url, is_local) comes
from vtx's ``provider_catalog``.  ``ProviderSpec`` is kept for vtx_claw's
internal use (backend, thinking_style, strip_model_prefixes, etc.),
but the master list of providers is vtx, not duplicated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic.alias_generators import to_snake


@dataclass(frozen=True)
class ProviderSpec:
    """One LLM provider's metadata. See PROVIDERS below for real examples.

    Placeholders in env_extras values:
      {api_key}  — the user's API key
      {api_base} — api_base from config, or this spec's default_api_base
    """

    # identity
    name: str  # config field name, e.g. "dashscope"
    keywords: tuple[str, ...]  # model-name keywords for matching (lowercase)
    env_key: str  # env var for API key, e.g. "DASHSCOPE_API_KEY"
    display_name: str = ""  # shown in `vtx_claw status`

    # which provider implementation to use
    # "openai_compat" | "anthropic" | "azure_openai" | "openai_codex" | "github_copilot" | "bedrock"
    backend: str = "openai_compat"

    # extra env vars / request headers supplied by the provider integration.
    env_extras: tuple[tuple[str, str], ...] = ()
    default_extra_headers: tuple[tuple[str, str], ...] = ()

    # gateway / local detection
    is_gateway: bool = False  # routes any model (OpenRouter, AiHubMix)
    is_local: bool = False  # local deployment (vLLM, Ollama)
    detect_by_key_prefix: str = ""  # match api_key prefix, e.g. "sk-or-"
    detect_by_base_keyword: str = ""  # match substring in api_base URL
    default_api_base: str = ""  # OpenAI-compatible base URL for this provider

    # gateway behavior
    strip_model_prefix: bool = False  # strip "provider/" before sending to gateway
    strip_model_prefixes: tuple[str, ...] = ()  # strip only when the first model segment matches
    supports_max_completion_tokens: bool = False

    # per-model param overrides, e.g. (("kimi-k2.5", {"temperature": 1.0}),)
    model_overrides: tuple[tuple[str, dict[str, Any]], ...] = ()

    # OAuth-based providers (e.g., OpenAI Codex) don't use API keys
    is_oauth: bool = False

    # Direct providers skip API-key validation (user supplies everything)
    is_direct: bool = False

    # Provider is listed for shared credentials but cannot serve chat completions.
    is_transcription_only: bool = False

    # Provider supports cache_control on content blocks (e.g. Anthropic prompt caching)
    supports_prompt_caching: bool = False

    # How to inject the thinking on/off toggle into extra_body.
    # ""              — no extra_body needed (default)
    # "thinking_type" — {"thinking": {"type": "enabled"/"disabled"}}
    #                   (DeepSeek, VolcEngine, BytePlus)
    # "enable_thinking" — {"enable_thinking": true/false}  (DashScope)
    # "reasoning_split" — {"reasoning_split": true/false}  (MiniMax)
    thinking_style: str = ""

    # Gateway-native reasoning control to pair with model-level thinking styles.
    # "reasoning_effort" — {"reasoning": {"effort": <none|minimal|...>}}
    #                      (OpenRouter)
    gateway_reasoning_style: str = ""

    # When True, treat the "reasoning" response field as formal content
    # when "content" is empty.  Only set this for providers (e.g. StepFun)
    # whose API returns the actual answer in "reasoning" instead of "content".
    reasoning_as_content: bool = False

    # Map user-supplied reasoning_effort (OpenAI vocab: minimal/low/medium/high)
    # to the value this provider accepts on the wire. Set when the provider's
    # accepted set differs from OpenAI's. An empty mapped value omits the kwarg.
    # Mistral: only "high"/"none" — low/minimal map to "none", medium maps to "high".
    reasoning_effort_remap: tuple[tuple[str, str], ...] = ()

    # Models whose API rejects the reasoning_effort kwarg because reasoning is
    # implicit (Magistral always reasons; sending the kwarg returns HTTP 400).
    # Substring match against the wire model name (lowercased).
    implicit_reasoning_models: tuple[str, ...] = ()

    # When the model returns content as a list of {"type":"thinking",...} +
    # {"type":"text",...} blocks, extract the thinking text into
    # reasoning_content. Mistral's Magistral / reasoning-enabled responses use
    # this shape.
    extract_thinking_blocks: bool = False

    # Strip ``reasoning_content`` from assistant history messages before
    # sending. Mistral validates its request schema strictly and 400s on
    # any extra fields; other providers (DeepSeek) require this key on the
    # wire to keep thinking-mode history intact.
    strip_history_reasoning_content: bool = False

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()


# ---------------------------------------------------------------------------
# Lookup helpers — backed by vtx's provider catalog
# ---------------------------------------------------------------------------


def list_providers() -> tuple[ProviderSpec, ...]:
    """List all known providers from vtx's catalog, plus custom/dynamic specs.

    Returns a tuple so existing callers that iterate ``PROVIDERS`` keep working.
    """
    result: list[ProviderSpec] = []
    try:
        from vtx.llm import provider_catalog as _vtx_providers

        for info in _vtx_providers.list_providers():
            spec = _spec_from_vtx_catalog(info.slug)
            if spec is not None:
                result.append(spec)
    except Exception:
        pass
    return tuple(result)


def find_by_name(name: str) -> ProviderSpec | None:
    """Find a provider spec by config field name, e.g. "dashscope".

    Source of truth is vtx's provider catalog.  Only falls back to
    ``create_dynamic_spec`` for truly custom (user-defined) providers
    that don't appear in either catalog.
    """
    normalized = to_snake(name.replace("-", "_"))
    spec = _spec_from_vtx_catalog(name)
    if spec is not None:
        return spec
    # Check if a dynamic spec was registered for a custom provider
    for existing in _DYNAMIC_SPECS:
        if existing.name == normalized:
            return existing
    return None


# In-memory registry for dynamically registered specs (custom providers)
_DYNAMIC_SPECS: list[ProviderSpec] = []


def _register_dynamic_spec(spec: ProviderSpec) -> None:
    """Register a dynamic ProviderSpec so find_by_name can find it."""
    for i, existing in enumerate(_DYNAMIC_SPECS):
        if existing.name == spec.name:
            _DYNAMIC_SPECS[i] = spec
            return
    _DYNAMIC_SPECS.append(spec)


def create_dynamic_spec(name: str, *, thinking_style: str = "") -> ProviderSpec:
    """Create a dynamic ProviderSpec for custom user-defined providers."""
    normalized = to_snake(name.replace("-", "_"))
    strip_prefixes = tuple(dict.fromkeys((name, normalized)))
    return ProviderSpec(
        name=normalized,
        keywords=(),
        env_key="",
        display_name=name.title(),
        backend="openai_compat",
        is_direct=True,
        strip_model_prefixes=strip_prefixes,
        thinking_style=thinking_style,
    )


def _spec_from_vtx_catalog(name: str) -> ProviderSpec | None:
    """Derive a ``ProviderSpec`` from vtx's provider catalog.

    Returns ``None`` if *name* is not found in vtx's catalog.
    """
    try:
        from vtx.llm import provider_catalog as _vtx_providers

        info = _vtx_providers.get(name)
        if info is None:
            return None
        normalized = to_snake(name.replace("-", "_"))
        family_to_backend = {"anthropic": "anthropic", "supercode": "supercode"}
        backend = family_to_backend.get(info.family, "openai_compat")
        return ProviderSpec(
            name=normalized,
            keywords=(normalized,),
            env_key=info.api_key_env or "",
            display_name=info.display_name,
            backend=backend,
            default_api_base=info.base_url or "",
            is_local=info.is_local,
            is_direct=info.api_key_optional and not info.is_local,
            strip_model_prefixes=(name, normalized),
        )
    except Exception:
        return None
