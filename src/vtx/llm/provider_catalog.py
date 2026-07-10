"""Load LLM provider catalog from provider.yaml.

Clients can add their own providers without editing this file:

* Drop a YAML file into ``~/.vtx/providers/*.yaml`` (same schema as a single
  ``providers`` entry). Files are auto-loaded on first catalog access.
* Or call :func:`register_custom_provider` from Python before the first lookup.

Custom providers are merged into the real catalog, so they're visible to
:func:`get`, :func:`list_providers`, :func:`find_model`,
:func:`detect_provider_from_env`, and the dynamic model fetcher.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import ApiType, Model

logger = logging.getLogger(__name__)


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
    max_tokens: int | None = None
    supports_thinking: bool = False
    fetch_models: bool = False
    models_endpoint: str = "/models"
    headers: dict[str, str] = field(default_factory=dict)
    openmodelendpoint: bool = False
    model_parser: ModelParserConfig = field(default_factory=ModelParserConfig)


_YAML_PATH = Path(__file__).parent / "provider.yaml"
_cache: dict[str, ProviderInfo] | None = None
_order_cache: list[str] | None = None

# Custom providers registered at runtime (highest precedence).
_custom_cache: dict[str, ProviderInfo] = {}


def _parse_entry(entry: dict) -> ProviderInfo:
    """Build a :class:`ProviderInfo` from a raw catalog dict entry."""
    slug = entry["slug"]
    parser_data = entry.get("model_parser") or {}
    parser = ModelParserConfig(
        array_path=parser_data.get("array_path", "data"),
        id_field=parser_data.get("id_field", "id"),
        name_field=parser_data.get("name_field", "name"),
        context_field=parser_data.get("context_field", "context_length"),
        output_field=parser_data.get("output_field", "max_completion_tokens"),
        cooldown_minutes=parser_data.get("cooldown_minutes", 60),
    )
    return ProviderInfo(
        slug=slug,
        display_name=entry.get("display_name", slug),
        description=entry.get("description", ""),
        family=entry["family"],
        base_url=entry.get("base_url"),
        api_key_env=entry.get("api_key_env"),
        known_models=tuple(entry.get("known_models", [])),
        supports_tools=entry.get("supports_tools", True),
        supports_vision=entry.get("supports_vision", False),
        api_key_optional=entry.get("api_key_optional", False),
        is_local=entry.get("is_local", False),
        max_tokens=entry.get("max_tokens"),
        supports_thinking=entry.get("supports_thinking", False),
        fetch_models=entry.get("fetch_models", False),
        models_endpoint=entry.get("models_endpoint", "/models"),
        headers=entry.get("headers") or {},
        openmodelendpoint=entry.get("openmodelendpoint", False),
        model_parser=parser,
    )


def _load_builtin() -> dict[str, ProviderInfo]:
    with open(_YAML_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {e["slug"]: _parse_entry(e) for e in data.get("providers", [])}


def _load_builtin() -> dict[str, ProviderInfo]:
    with open(_YAML_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {e["slug"]: _parse_entry(e) for e in data.get("providers", [])}


def _get_order() -> list[str]:
    global _order_cache
    if _order_cache is not None:
        return _order_cache
    _ensure_custom_files_loaded()
    with open(_YAML_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _order_cache = [e["slug"] for e in data.get("providers", [])]
    # Append slugs of runtime-registered custom providers not already present.
    for slug in _custom_cache:
        if slug not in _order_cache:
            _order_cache.append(slug)
    return _order_cache


DEFAULT_PROVIDER_SLUG = "openai"


def get(slug: str) -> ProviderInfo | None:
    _ensure_custom_files_loaded()
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

    # Check OAuth-backed providers by probing their credential file
    for slug in order:
        p = providers[slug]
        if p.is_local:
            continue
        if slug == "supercode":
            try:
                from vtx.llm.oauth.supercode import is_supercode_logged_in

                if is_supercode_logged_in():
                    return p
            except Exception:
                pass

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
        "supercode": ApiType(ApiType.SUPERCODE),
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


# =================================================================================================
# Custom providers
# =================================================================================================
#
# Clients can add their own providers without editing ``provider.yaml``. Three
# supported paths:
#
#   1. Drop a YAML file into ``~/.vtx/providers/*.yaml`` (global / user-wide).
#   2. Drop a YAML file into ``.vtx/providers/*.yaml`` in the current working
#      directory (project-local; handy for committing a provider per-repo).
#   3. Call :func:`register_custom_provider` from Python before the first
#      catalog lookup. This is handy for programmatic/test setups.
#
# All file-based routes load the global dir first, then the project-local dir
# (project-local overrides global). Each file uses the same schema as a single
# entry in ``provider.yaml`` (``slug``, ``display_name``, ``family``,
# ``base_url``, ...). Files are loaded in sorted filename order; a slug already
# defined by a built-in provider or an earlier file is overwritten.
#
# Both file and runtime routes funnel through :func:`register_custom_provider`,
# so they share the same validation and override semantics.


def custom_providers_dir() -> Path:
    """User-wide directory where custom-provider YAML files live (``~/.vtx/providers``)."""
    from ..config import get_config_dir

    return get_config_dir() / "providers"


def local_custom_providers_dir() -> Path:
    """Project-local directory for custom-provider YAML files (``<cwd>/.vtx/providers``)."""
    return Path.cwd() / ".vtx" / "providers"


def _load_custom_provider_files() -> list[ProviderInfo]:
    """Parse every ``*.yaml`` file across the global then local providers dirs."""
    loaded: list[ProviderInfo] = []
    for directory in (custom_providers_dir(), local_custom_providers_dir()):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            if not path.is_file():
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
            except Exception as exc:
                logger.warning("Failed to read custom provider file %s: %s", path, exc)
                continue
            if not data:
                continue
            # A file may define either a single mapping or a ``providers`` list.
            entries = data.get("providers", [data]) if isinstance(data, dict) else []
            if isinstance(data, dict) and "providers" not in data:
                entries = [data]
            for entry in entries:
                try:
                    loaded.append(_parse_entry(entry))
                except (KeyError, TypeError) as exc:
                    logger.warning("Skipping invalid custom provider in %s: %s", path, exc)
    return loaded


def register_custom_provider(
    slug: str,
    *,
    display_name: str,
    family: str,
    description: str = "",
    base_url: str | None = None,
    api_key_env: str | None = None,
    known_models: list[str] | None = None,
    supports_tools: bool = True,
    supports_vision: bool = False,
    api_key_optional: bool = False,
    is_local: bool = False,
    max_tokens: int | None = None,
    supports_thinking: bool = False,
    fetch_models: bool = False,
    models_endpoint: str = "/models",
    headers: dict[str, str] | None = None,
    openmodelendpoint: bool = False,
    model_parser: dict | None = None,
) -> ProviderInfo:
    """Register (or replace) a custom provider at runtime.

    The provider becomes part of the catalog immediately and is visible to
    :func:`get`, :func:`list_providers`, :func:`find_model`,
    :func:`detect_provider_from_env`, and the dynamic model fetcher.
    """
    global _cache
    if family not in ("openai_compat", "anthropic", "supercode"):
        raise ValueError(
            f"Unsupported family {family!r}; expected "
            "'openai_compat', 'anthropic', or 'supercode'."
        )
    parser_data = model_parser or {}
    info = ProviderInfo(
        slug=slug,
        display_name=display_name,
        description=description,
        family=family,
        base_url=base_url,
        api_key_env=api_key_env,
        known_models=tuple(known_models or []),
        supports_tools=supports_tools,
        supports_vision=supports_vision,
        api_key_optional=api_key_optional,
        is_local=is_local,
        max_tokens=max_tokens,
        supports_thinking=supports_thinking,
        fetch_models=fetch_models,
        models_endpoint=models_endpoint,
        headers=headers or {},
        openmodelendpoint=openmodelendpoint,
        model_parser=ModelParserConfig(
            array_path=parser_data.get("array_path", "data"),
            id_field=parser_data.get("id_field", "id"),
            name_field=parser_data.get("name_field", "name"),
            context_field=parser_data.get("context_field", "context_length"),
            output_field=parser_data.get("output_field", "max_completion_tokens"),
            cooldown_minutes=parser_data.get("cooldown_minutes", 60),
        ),
    )
    _custom_cache[slug] = info
    _cache = None  # force rebuild on next access
    global _order_cache
    _order_cache = None  # force order rebuild so new slug is listed
    _sync_dynamic_provider(info)
    return info


def _sync_dynamic_provider(info: ProviderInfo) -> None:
    """Mirror a custom provider into the dynamic model fetcher if it has a URL."""
    if not info.base_url:
        return
    try:
        from .dynamic_models import ApiType as _DynApiType
        from .dynamic_models import DynamicProviderConfig, register_dynamic_provider

        family_api = {
            "openai_compat": _DynApiType(_DynApiType.OPENAI_COMPLETIONS),
            "anthropic": _DynApiType(_DynApiType.ANTHROPIC),
            "supercode": _DynApiType(_DynApiType.SUPERCODE),
        }
        register_dynamic_provider(
            DynamicProviderConfig(
                name=info.slug,
                base_url=info.base_url,
                env_var=info.api_key_env or "",
                api=family_api.get(info.family, _DynApiType(_DynApiType.OPENAI_COMPLETIONS)),
                headers=dict(info.headers),
                api_key_optional=info.api_key_optional,
                openmodelendpoint=info.openmodelendpoint,
            )
        )
    except Exception as exc:
        logger.debug("Could not sync custom provider %s to dynamic fetcher: %s", info.slug, exc)


def load_custom_providers() -> int:
    """Load all custom provider YAML files, registering each.

    Calling this is optional: catalog lookups auto-load custom files on first
    access. Use it when you want eager loading / a count of loaded providers.
    Each provider is also mirrored into the dynamic model fetcher so it shows
    up in the ``/model`` picker and supports ``/model refresh``.
    """
    count = 0
    for info in _load_custom_provider_files():
        _custom_cache[info.slug] = info
        _sync_dynamic_provider(info)
        count += 1
    if count:
        global _cache
        _cache = None
    return count


def list_custom_providers() -> list[ProviderInfo]:
    """Return currently registered custom providers."""
    return list(_custom_cache.values())


def clear_custom_providers() -> None:
    """Remove all runtime-registered custom providers."""
    global _cache
    for slug in list(_custom_cache):
        _unsync_dynamic_provider(slug)
    _custom_cache.clear()
    _cache = None


def _unsync_dynamic_provider(slug: str) -> None:
    """Remove a custom provider from the dynamic model fetcher, if present."""
    try:
        from .dynamic_models import DYNAMIC_PROVIDERS

        DYNAMIC_PROVIDERS.pop(slug, None)
    except Exception:
        pass


# Auto-load custom provider files on first catalog access (lazily).
_loaded_custom_files = False


def _ensure_custom_files_loaded() -> None:
    global _loaded_custom_files
    if _loaded_custom_files:
        return
    _loaded_custom_files = True
    load_custom_providers()


# Patch _load so file-based custom providers are merged in automatically.
def _load() -> dict[str, ProviderInfo]:
    _ensure_custom_files_loaded()
    providers = _load_builtin()
    providers.update(_custom_cache)
    return providers


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
