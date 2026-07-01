"""Create LLM providers from config — via vtx."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vtx.llm.base import ProviderConfig
from vtx.llm.providers import get_provider_class, resolve_provider_api_type
from vtx_claw.config.schema import Config, InlineFallbackConfig, ModelPresetConfig
from vtx_claw.config.schema import ProviderConfig as ClawProviderConfig
from vtx_claw.providers.registry import ProviderSpec, create_dynamic_spec, find_by_name


@dataclass(frozen=True)
class ProviderSnapshot:
    """Snapshot of the resolved provider chain for a given config/model."""

    provider: Any  # LLMProvider
    model: str
    context_window_tokens: int
    signature: tuple[object, ...]


def _resolve_model_preset(
    config: Config, *, preset_name: str | None = None, preset: ModelPresetConfig | None = None
) -> ModelPresetConfig:
    return preset if preset is not None else config.resolve_preset(preset_name)


def _provider_extra_headers(
    spec: ProviderSpec | None, provider_config: ClawProviderConfig | None
) -> dict[str, str] | None:
    headers = dict(spec.default_extra_headers) if spec else {}
    if provider_config and provider_config.extra_headers:
        headers.update(provider_config.extra_headers)
    return headers or None


def _make_provider_core(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
    model: str | None = None,
) -> Any:
    """Create a plain LLM provider without failover wrapping — via vtx."""
    resolved = _resolve_model_preset(config, preset_name=preset_name, preset=preset)
    model = model or resolved.model
    provider_name = config.get_provider_name(model, preset=resolved)
    p = config.get_provider(model, preset=resolved)
    if not provider_name or not p:
        raise ValueError(f"No configured provider found for model '{model}'.")
    spec = find_by_name(provider_name)
    if not spec and p:
        if not p.api_base:
            raise ValueError(f"Provider '{provider_name}' requires api_base in config.")
        spec = create_dynamic_spec(
            provider_name, thinking_style=(p.thinking_style or "") if p else ""
        )
    if spec and spec.is_transcription_only:
        raise ValueError(f"Provider '{provider_name}' only supports transcription.")

    # Resolve provider type from vtx's catalog
    api_type = resolve_provider_api_type(spec.backend if spec else None)
    provider_class = get_provider_class(api_type)

    provider_config = ProviderConfig(
        api_key=p.api_key if p else None,
        base_url=config.get_api_base(model, preset=resolved),
        model=model,
        provider=provider_name,
        thinking_level="high",
    )

    provider = provider_class(provider_config)
    try:
        provider.generation = resolved.to_generation_settings()
    except (TypeError, AttributeError):
        pass
    return provider


def _inline_fallback_preset(
    primary: ModelPresetConfig, fallback: InlineFallbackConfig
) -> ModelPresetConfig:
    return ModelPresetConfig(
        model=fallback.model,
        provider=fallback.provider,
        max_tokens=fallback.max_tokens if fallback.max_tokens is not None else primary.max_tokens,
        context_window_tokens=(
            fallback.context_window_tokens
            if fallback.context_window_tokens is not None
            else primary.context_window_tokens
        ),
        temperature=fallback.temperature
        if fallback.temperature is not None
        else primary.temperature,
        reasoning_effort=fallback.reasoning_effort,
    )


def _resolve_fallback_presets(
    config: Config, primary: ModelPresetConfig
) -> list[ModelPresetConfig]:
    presets: list[ModelPresetConfig] = []
    for fallback in config.agents.defaults.fallback_models:
        if isinstance(fallback, str):
            presets.append(config.model_presets[fallback])
        else:
            presets.append(_inline_fallback_preset(primary, fallback))
    return presets


def make_provider(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
    model: str | None = None,
) -> Any:
    """Create the LLM provider implied by config."""
    resolved = _resolve_model_preset(config, preset_name=preset_name, preset=preset)
    provider = _make_provider_core(config, preset_name=preset_name, preset=preset, model=model)
    return provider


def build_provider_snapshot(
    config: Config, *, preset_name: str | None = None, preset: ModelPresetConfig | None = None
) -> ProviderSnapshot:
    resolved = _resolve_model_preset(config, preset_name=preset_name, preset=preset)
    return ProviderSnapshot(
        provider=make_provider(config, preset=resolved),
        model=resolved.model,
        context_window_tokens=resolved.context_window_tokens,
        signature=provider_signature(config, preset=resolved),
    )


def provider_signature(
    config: Config, *, preset_name: str | None = None, preset: ModelPresetConfig | None = None
) -> tuple[object, ...]:
    """Return the config fields that affect the active provider chain."""
    resolved = _resolve_model_preset(config, preset_name=preset_name, preset=preset)
    p = config.get_provider(resolved.model, preset=resolved)
    return (
        resolved.model,
        resolved.provider,
        p.api_key if p else None,
        p.api_base if p else None,
        resolved.max_tokens,
        resolved.temperature,
    )


def load_provider_snapshot(
    config_path: Path | None = None, *, preset_name: str | None = None
) -> ProviderSnapshot:
    from vtx_claw.config.loader import load_config, resolve_config_env_vars

    return build_provider_snapshot(
        resolve_config_env_vars(load_config(config_path)), preset_name=preset_name
    )


__all__ = [
    "ProviderSnapshot",
    "build_provider_snapshot",
    "load_provider_snapshot",
    "make_provider",
    "provider_signature",
]
