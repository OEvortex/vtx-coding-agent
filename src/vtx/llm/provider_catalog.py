"""Load LLM provider catalog from provider.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import ApiType, Model


@dataclass(frozen=True)
class ModelParserConfig:
    array_path: str = "data"
    id_field: str = "id"
    name_field: str = "name"
    context_field: str = "context_length"
    output_field: str = "max_completion_tokens"
    cooldown_minutes: int = 60


@dataclass(frozen=True)
class ProviderInfo:
    slug: str
    display_name: str
    description: str
    family: str
    base_url: str | None = None
    api_key_env: str | None = None
    known_models: tuple[str, ...] = ()
    supports_tools: bool = True
    supports_vision: bool = False
    api_key_optional: bool = False
    is_local: bool = False
    max_tokens: int = 8192
    supports_thinking: bool = False
    fetch_models: bool = False
    models_endpoint: str = "/models"
    headers: dict[str, str] = field(default_factory=dict)
    openmodelendpoint: bool = False
    model_parser: ModelParserConfig = field(default_factory=ModelParserConfig)


_YAML_PATH = Path(__file__).parent / "provider.yaml"
_cache: dict[str, ProviderInfo] | None = None
_order_cache: list[str] | None = None


def _load() -> dict[str, ProviderInfo]:
    global _cache
    if _cache is not None:
        return _cache

    with open(_YAML_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    providers: dict[str, ProviderInfo] = {}
    for entry in data.get("providers", []):
        parser_data = entry.get("model_parser") or {}
        parser = ModelParserConfig(
            array_path=parser_data.get("array_path", "data"),
            id_field=parser_data.get("id_field", "id"),
            name_field=parser_data.get("name_field", "name"),
            context_field=parser_data.get("context_field", "context_length"),
            output_field=parser_data.get("output_field", "max_completion_tokens"),
            cooldown_minutes=parser_data.get("cooldown_minutes", 60),
        )
        p = ProviderInfo(
            slug=entry["slug"],
            display_name=entry["display_name"],
            description=entry["description"],
            family=entry["family"],
            base_url=entry.get("base_url"),
            api_key_env=entry.get("api_key_env"),
            known_models=tuple(entry.get("known_models", [])),
            supports_tools=entry.get("supports_tools", True),
            supports_vision=entry.get("supports_vision", False),
            api_key_optional=entry.get("api_key_optional", False),
            is_local=entry.get("is_local", False),
            max_tokens=entry.get("max_tokens", 8192),
            supports_thinking=entry.get("supports_thinking", False),
            fetch_models=entry.get("fetch_models", False),
            models_endpoint=entry.get("models_endpoint", "/models"),
            headers=entry.get("headers") or {},
            openmodelendpoint=entry.get("openmodelendpoint", False),
            model_parser=parser,
        )
        providers[p.slug] = p

    _cache = providers
    return _cache


def _get_order() -> list[str]:
    global _order_cache
    if _order_cache is not None:
        return _order_cache
    with open(_YAML_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _order_cache = [e["slug"] for e in data.get("providers", [])]
    return _order_cache


DEFAULT_PROVIDER_SLUG = "openai"


def get(slug: str) -> ProviderInfo | None:
    return _load().get(slug)


def list_providers() -> list[ProviderInfo]:
    order = _get_order()
    providers = _load()
    return [providers[s] for s in order if s in providers]


def detect_provider_from_env() -> ProviderInfo:
    providers = _load()
    order = _get_order()

    explicit = os.getenv("VTX_PROVIDER", "").strip().lower()
    if explicit and explicit in providers:
        return providers[explicit]

    for slug in order:
        p = providers[slug]
        if p.is_local:
            continue
        if p.api_key_env and os.getenv(p.api_key_env):
            return p

    for slug in order:
        p = providers[slug]
        if p.is_local and p.api_key_optional:
            return p

    return providers[DEFAULT_PROVIDER_SLUG]


def is_provider_configured(p: ProviderInfo) -> bool:
    if p.api_key_optional:
        return True
    if p.api_key_env is None:
        return True
    return bool(os.getenv(p.api_key_env))


def _provider_info_to_model(p: ProviderInfo, model_id: str) -> Model:
    family_to_api = {
        "openai_compat": ApiType(ApiType.OPENAI_SDK),
        "anthropic": ApiType(ApiType.ANTHROPIC),
    }
    from .context_length import context_length_manager

    limits = context_length_manager.get_limits(model_id)
    is_matched = model_id in context_length_manager._limits or any(
        model_id.lower() in k.lower() or k.lower() in model_id.lower()
        for k in context_length_manager._limits
    )

    if is_matched:
        max_tokens = limits.output
        supports_images = limits.supports_vision
        supports_thinking = limits.supports_reasoning
        context_window = limits.context
        supports_tools = limits.supports_tools
        supports_audio = limits.supports_audio
    else:
        max_tokens = p.max_tokens
        supports_images = p.supports_vision
        supports_thinking = p.supports_thinking
        context_window = limits.context
        supports_tools = p.supports_tools
        supports_audio = False

    return Model(
        id=model_id,
        provider=p.slug,
        api=family_to_api[p.family],
        base_url=p.base_url or "",
        max_tokens=max_tokens,
        supports_images=supports_images,
        supports_thinking=supports_thinking,
        context_window=context_window,
        supports_tools=supports_tools,
        supports_audio=supports_audio,
    )


def get_all_catalog_models() -> list[Model]:
    from .model_fetcher import get_fetched_models

    models: list[Model] = []
    for p in _load().values():
        fetched = get_fetched_models(p)
        if fetched:
            models.extend(fetched)
        else:
            for model_id in p.known_models:
                models.append(_provider_info_to_model(p, model_id))
    return models


def find_model(model_id: str, provider: str | None = None) -> Model | None:
    from .model_fetcher import get_fetched_models

    providers = _load()
    if provider:
        p = providers.get(provider)
        if p:
            fetched = get_fetched_models(p)
            if fetched:
                for m in fetched:
                    if m.id == model_id:
                        return m
            if model_id in p.known_models:
                return _provider_info_to_model(p, model_id)

    for p in providers.values():
        fetched = get_fetched_models(p)
        if fetched:
            for m in fetched:
                if m.id == model_id:
                    return m
        if model_id in p.known_models:
            return _provider_info_to_model(p, model_id)

    return None
