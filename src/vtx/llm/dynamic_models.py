"""
Dynamic model fetching and caching for OpenAI-compatible providers.

Many modern LLM gateways (Kilo, OpenCode Zen, Airouter, TokenRouter, etc.) expose
their model catalog via the standard OpenAI ``GET /v1/models`` endpoint and rotate
it frequently. Hard-coding a model list in ``models.py`` would go stale the same
day it ships, so this module:

1. Derives :data:`DYNAMIC_PROVIDERS` from ``provider.yaml`` — every provider
   with a ``base_url`` becomes a dynamic provider. Providers with
   ``api_key_optional: true`` (e.g. ollama) are recognized as not requiring
   any key.
2. Fetches ``<base_url>/models`` on demand with short retries.
3. Persists the result to ``~/.vtx/models/<provider>.json`` with a TTL so
   the UI stays responsive when the network is slow or offline.
4. Falls back to the cached snapshot on network failure (stale-while-revalidate).
5. Filters free vs. paid models using the same dual heuristic as the
   ``@neilurk12/pi-free-models`` extension that ships with pi:

   - For providers that expose pricing (Kilo/OpenRouter-style), a model is free
     iff ``cost.input == 0 and cost.output == 0`` (or its name contains
     ``"free"``).
   - For providers without pricing (OpenCode Zen, etc.), a model is free iff
     its name contains ``"free"`` (case-insensitive).

The fetched catalog is merged into the static ``MODELS`` table by
:func:`get_all_models` so the rest of vtx (model picker, runtime, etc.) does
not need to know the difference.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .models import ApiType, Model

logger = logging.getLogger(__name__)

# =================================================================================================
# Configuration
# =================================================================================================

CACHE_DIR_NAME = "models"
CACHE_TTL_SECONDS = 60 * 60 * 6  # 6h; matches pi-free-models default behaviour
FETCH_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class DynamicProviderConfig:
    """Static metadata for a provider whose models are fetched at runtime."""

    name: str
    base_url: str
    env_var: str
    api: ApiType = field(default_factory=lambda: ApiType(ApiType.OPENAI_COMPLETIONS))
    # Extra headers required by some gateways (e.g. Kilo's editor banner).
    headers: dict[str, str] = field(default_factory=dict)
    # If True, the provider does not check the Authorization header at all
    # (e.g. a local server like ollama). Requests are sent with no key.
    api_key_optional: bool = False
    # If True, the provider's ``/models`` catalog endpoint is publicly
    # accessible (no key required for discovery). Inference still needs a
    # real key; this only relaxes the auth gate for the catalog fetch.
    openmodelendpoint: bool = False
    # Some gateways return a bare JSON array instead of ``{"data": [...]}``.
    response_format: str = "openai"  # "openai" | "bare_array"


_FAMILY_TO_API: dict[str, ApiType] = {
    "openai_compat": ApiType(ApiType.OPENAI_COMPLETIONS),
    "anthropic": ApiType(ApiType.ANTHROPIC),
}


def _build_dynamic_providers() -> dict[str, DynamicProviderConfig]:
    """Build the dynamic-provider registry from ``provider.yaml``.

    Every provider with a ``base_url`` becomes a dynamic provider. Extra
    request headers and the ``api_key_optional`` flag are passed through
    unchanged — they are how the catalog tells us "this provider does not
    require a key at all" (e.g. ollama).
    """
    from .provider_catalog import list_providers

    out: dict[str, DynamicProviderConfig] = {}
    for p in list_providers():
        if not p.base_url:
            continue
        out[p.slug] = DynamicProviderConfig(
            name=p.slug,
            base_url=p.base_url,
            env_var=p.api_key_env or "",
            api=_FAMILY_TO_API.get(p.family, ApiType(ApiType.OPENAI_COMPLETIONS)),
            headers=dict(p.headers),
            api_key_optional=p.api_key_optional,
            openmodelendpoint=p.openmodelendpoint,
        )
    return out


# Built-in dynamic providers, derived from provider.yaml. Users can register
# more at runtime by calling :func:`register_dynamic_provider` before the first
# model lookup.
DYNAMIC_PROVIDERS: dict[str, DynamicProviderConfig] = _build_dynamic_providers()


def register_dynamic_provider(config: DynamicProviderConfig) -> None:
    """Register or replace a dynamic provider at runtime."""
    DYNAMIC_PROVIDERS[config.name] = config


def get_dynamic_provider(name: str) -> DynamicProviderConfig | None:
    return DYNAMIC_PROVIDERS.get(name)


# =================================================================================================
# Catalog types
# =================================================================================================


@dataclass
class DynamicModelEntry:
    """A single model returned by a dynamic provider's /models endpoint."""

    id: str
    name: str
    context_window: int | None = None
    max_tokens: int | None = None
    supports_images: bool = False
    supports_thinking: bool = False
    is_free: bool = False
    pricing_known: bool = False  # False = name-based free detection only
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CachedCatalog:
    """Serialized snapshot of a provider's model catalog."""

    provider: str
    fetched_at: float
    models: list[DynamicModelEntry]

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "fetched_at": self.fetched_at,
            "models": [asdict(m) for m in self.models],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CachedCatalog:
        models_data = data.get("models", [])
        models = [DynamicModelEntry(**m) for m in models_data if isinstance(m, dict)]
        return cls(
            provider=data.get("provider", ""),
            fetched_at=float(data.get("fetched_at", 0.0)),
            models=models,
        )


# =================================================================================================
# Cache IO
# =================================================================================================


def get_cache_dir() -> Path:
    """Return the directory where per-provider model catalogs are cached."""
    from ..config import get_config_dir

    base = os.environ.get("VTX_MODELS_CACHE_DIR")
    if base:
        return Path(base)
    return get_config_dir() / CACHE_DIR_NAME


def _cache_path(provider: str) -> Path:
    safe = provider.replace("/", "_").replace("..", "_")
    return get_cache_dir() / f"{safe}.json"


def _read_cache(provider: str) -> CachedCatalog | None:
    path = _cache_path(provider)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Failed to read model cache for %s: %s", provider, exc)
        return None
    try:
        return CachedCatalog.from_dict(data)
    except (TypeError, ValueError) as exc:
        logger.debug("Discarding corrupt cache for %s: %s", provider, exc)
        return None


def _write_cache(catalog: CachedCatalog) -> None:
    path = _cache_path(catalog.provider)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(catalog.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("Failed to write model cache for %s: %s", catalog.provider, exc)


# =================================================================================================
# Fetching
# =================================================================================================


def _parse_pricing(raw: dict[str, Any]) -> tuple[float, float, bool]:
    """Extract (input_cost, output_cost, pricing_known) from a raw model entry."""
    pricing = raw.get("pricing")
    if not isinstance(pricing, dict):
        return 0.0, 0.0, False
    try:
        prompt = float(pricing.get("prompt", 0) or 0)
        completion = float(pricing.get("completion", 0) or 0)
    except (TypeError, ValueError):
        return 0.0, 0.0, False
    return prompt, completion, True


def _supports_images(raw: dict[str, Any]) -> bool:
    arch = raw.get("architecture")
    if isinstance(arch, dict):
        modalities = arch.get("input_modalities")
        if isinstance(modalities, list) and "image" in modalities:
            return True
    modalities = raw.get("input_modalities")
    return isinstance(modalities, list) and "image" in modalities


def _entry_id(raw: dict[str, Any]) -> str:
    model_id = raw.get("id")
    if isinstance(model_id, str) and model_id:
        return model_id
    return raw.get("name") or raw.get("model") or ""


def _entry_name(raw: dict[str, Any], model_id: str) -> str:
    name = raw.get("name")
    if isinstance(name, str) and name and name != model_id:
        # Some providers prefix the name with the model id; strip it.
        if ":" in name:
            candidate = name.split(":", 1)[1].strip()
            if candidate:
                return candidate
        return name
    return model_id


def _is_free_model(
    name: str, prompt_cost: float, completion_cost: float, pricing_known: bool
) -> bool:
    """Dual heuristic mirroring the pi free-models extension."""
    name_lower = name.lower()
    has_free_keyword = "free" in name_lower
    if pricing_known:
        zero_cost = prompt_cost == 0.0 and completion_cost == 0.0
        return zero_cost or has_free_keyword
    return has_free_keyword


def _raw_model_list(payload: Any, response_format: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [m for m in payload if isinstance(m, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [m for m in data if isinstance(m, dict)]
        models = payload.get("models")
        if isinstance(models, list):
            return [m for m in models if isinstance(m, dict)]
    return []


# Models.dev specifications URL
MODELS_DEV_URL = "https://models.dev/models.json"


async def _fetch_models_dev() -> dict[str, Any]:
    path = get_cache_dir() / "models_dev.json"
    try:
        if path.exists():
            stat = path.stat()
            # Cache for 24 hours
            if (time.time() - stat.st_mtime) < (60 * 60 * 24):
                return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("Failed to read models.dev cache: %s", exc)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(MODELS_DEV_URL)
            if response.is_success:
                data = response.json()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                return data
    except Exception as exc:
        logger.debug("Failed to fetch models.dev: %s", exc)

    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _read_models_dev_sync() -> dict[str, Any]:
    path = get_cache_dir() / "models_dev.json"
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _find_spec_in_models_dev(model_id: str, models_dev: dict[str, Any]) -> dict[str, Any] | None:
    model_id_lower = model_id.lower()
    for key, spec in models_dev.items():
        key_lower = key.lower()
        if key_lower == model_id_lower:
            return spec
        if "/" in key_lower:
            parts = key_lower.split("/", 1)
            if parts[1] == model_id_lower:
                return spec
    return None


def _parse_models(
    raw_models: list[dict[str, Any]], models_dev: dict[str, Any] | None = None
) -> list[DynamicModelEntry]:
    if models_dev is None:
        models_dev = _read_models_dev_sync()

    entries: list[DynamicModelEntry] = []
    for raw in raw_models:
        model_id = _entry_id(raw)
        if not model_id:
            continue
        # Skip embedding / image-only models
        output_modalities = raw.get("output_modalities")
        if isinstance(output_modalities, list) and "image" in output_modalities:
            continue
        arch = raw.get("architecture")
        if isinstance(arch, dict):
            modalities = arch.get("output_modalities")
            if isinstance(modalities, list) and "image" in modalities and "text" not in modalities:
                continue

        prompt_cost, completion_cost, pricing_known = _parse_pricing(raw)
        name = _entry_name(raw, model_id)

        spec = _find_spec_in_models_dev(model_id, models_dev)

        # 1. Context window
        context_window = None
        if spec:
            context_window = spec.get("limit", {}).get("context")
        if context_window is None:
            raw_ctx = raw.get("context_length")
            context_window = int(raw_ctx) if raw_ctx else None
        else:
            context_window = int(context_window)

        # 2. Max output tokens
        max_tokens = None
        if spec:
            max_tokens = spec.get("limit", {}).get("output")
        if max_tokens is None:
            raw_max = (
                raw.get("max_completion_tokens")
                or (raw.get("top_provider") or {}).get("max_completion_tokens")
                or raw.get("max_tokens")
            )
            max_tokens = int(raw_max) if raw_max else None
        else:
            max_tokens = int(max_tokens)

        if context_window and max_tokens and context_window - max_tokens <= 8192:
            max_tokens = min(16384, context_window)

        # 3. Supports thinking/reasoning
        supports_thinking = None
        if spec and "reasoning" in spec:
            supports_thinking = bool(spec.get("reasoning"))
        if supports_thinking is None:
            model_id_lower = model_id.lower()
            supports_thinking = bool(
                raw.get("supports_reasoning")
                or raw.get("reasoning")
                or "thinking" in model_id_lower
                or "reasoning" in model_id_lower
            )

        # 4. Supports images
        supports_images = None
        if spec:
            input_modalities = spec.get("modalities", {}).get("input", [])
            if input_modalities:
                supports_images = "image" in input_modalities
        if supports_images is None:
            supports_images = _supports_images(raw)

        entry = DynamicModelEntry(
            id=model_id,
            name=name,
            context_window=context_window,
            max_tokens=max_tokens,
            supports_images=supports_images,
            supports_thinking=supports_thinking,
            is_free=_is_free_model(name, prompt_cost, completion_cost, pricing_known),
            pricing_known=pricing_known,
            raw=raw,
        )
        entries.append(entry)
    return entries


async def _async_fetch_catalog(
    config: DynamicProviderConfig, *, api_key: str | None, force: bool = False
) -> CachedCatalog:
    """Fetch the live model list for a single provider, refreshing the cache."""
    if not force:
        cached = _read_cache(config.name)
        if cached and (time.time() - cached.fetched_at) < CACHE_TTL_SECONDS:
            return cached

    if not api_key and not config.api_key_optional and not config.openmodelendpoint:
        cached = _read_cache(config.name)
        if cached:
            return cached
        raise RuntimeError(
            f"Authentication required for {config.name}. "
            f"Set {config.env_var} or /login for this provider."
        )

    headers = {"Accept": "application/json", **config.headers}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    base = config.base_url.rstrip("/")
    url = f"{base}/models"

    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        cached = _read_cache(config.name)
        if cached:
            logger.debug("Falling back to stale cache for %s: %s", config.name, exc)
            return cached
        raise RuntimeError(f"Network error fetching models for {config.name}: {exc}") from exc

    if response.status_code == 401 or response.status_code == 403:
        cached = _read_cache(config.name)
        if cached:
            logger.debug("Auth error fetching %s; using cached snapshot", config.name)
            return cached
        raise RuntimeError(
            f"Authentication required for {config.name}. "
            f"Set {config.env_var} or /login for this provider."
        )

    if response.status_code >= 500:
        cached = _read_cache(config.name)
        if cached:
            return cached
        raise RuntimeError(
            f"Server error {response.status_code} fetching models for {config.name}"
        )

    if not response.is_success:
        cached = _read_cache(config.name)
        if cached:
            return cached
        raise RuntimeError(
            f"Failed to fetch models for {config.name}: "
            f"{response.status_code} {response.reason_phrase}"
        )

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        cached = _read_cache(config.name)
        if cached:
            return cached
        raise RuntimeError(f"Invalid JSON from {config.name} /models: {exc}") from exc

    raw_models = _raw_model_list(payload, config.response_format)
    if not raw_models:
        cached = _read_cache(config.name)
        if cached:
            return cached
        raise RuntimeError(f"No models returned by {config.name} /models")

    # Fetch models.dev specs cache
    import contextlib

    models_dev: dict[str, Any] | None = None
    with contextlib.suppress(Exception):
        models_dev = await _fetch_models_dev()

    catalog = CachedCatalog(
        provider=config.name, fetched_at=time.time(), models=_parse_models(raw_models, models_dev)
    )
    _write_cache(catalog)
    return catalog


# Public synchronous entry points
# -----------------------------------------------------------------------------


def _resolve_api_key(config: DynamicProviderConfig) -> str | None:
    # Delegate to the auth module so the env-var/stored-key priority lives in
    # exactly one place. Providers with api_key_optional (e.g. ollama) end
    # up with no key here; the caller is responsible for skipping the
    # Authorization header in that case.
    from .oauth.dynamic import get_dynamic_api_key

    return get_dynamic_api_key(config.name)


def get_provider_models(provider: str, *, force_refresh: bool = False) -> list[DynamicModelEntry]:
    """Return the cached/fetched model list for a single provider (sync)."""
    config = DYNAMIC_PROVIDERS.get(provider)
    if config is None:
        return []

    # If not forcing a refresh, return cached models if they exist.
    # In an active event loop (like the Textual app), we must avoid blocking/asyncio.run()
    # and always return the cache.
    if not force_refresh:
        cached = _read_cache(config.name)
        if cached:
            try:
                asyncio.get_running_loop()
                return list(cached.models)
            except RuntimeError:
                # No running loop, fallback to TTL check
                if (time.time() - cached.fetched_at) < CACHE_TTL_SECONDS:
                    return list(cached.models)

    api_key = _resolve_api_key(config)
    try:
        catalog = asyncio.run(_async_fetch_catalog(config, api_key=api_key, force=force_refresh))
    except RuntimeError as exc:
        logger.debug("Provider %s unavailable: %s", provider, exc)
        # Fallback to cache on error
        cached = _read_cache(config.name)
        if cached:
            return list(cached.models)
        return []
    return list(catalog.models)


async def aget_provider_models(
    provider: str, *, force_refresh: bool = False
) -> list[DynamicModelEntry]:
    """Async variant of :func:`get_provider_models`."""
    config = DYNAMIC_PROVIDERS.get(provider)
    if config is None:
        return []
    api_key = _resolve_api_key(config)
    catalog = await _async_fetch_catalog(config, api_key=api_key, force=force_refresh)
    return list(catalog.models)


def refresh_provider(provider: str) -> int:
    """Force-refresh a single provider's cache. Returns number of models cached."""
    config = DYNAMIC_PROVIDERS.get(provider)
    if config is None:
        raise ValueError(f"Unknown dynamic provider: {provider}")
    api_key = _resolve_api_key(config)
    catalog = asyncio.run(_async_fetch_catalog(config, api_key=api_key, force=True))
    return len(catalog.models)


def refresh_all_providers() -> dict[str, int]:
    """Force-refresh every known dynamic provider. Returns {name: model_count}."""
    results: dict[str, int] = {}
    for name, config in DYNAMIC_PROVIDERS.items():
        api_key = _resolve_api_key(config)
        try:
            catalog = asyncio.run(_async_fetch_catalog(config, api_key=api_key, force=True))
        except RuntimeError as exc:
            logger.debug("Skipping %s during refresh: %s", name, exc)
            results[name] = 0
            continue
        results[name] = len(catalog.models)
    return results


# =================================================================================================
# Integration with the static MODELS table
# =================================================================================================


def _to_static_model(provider: str, entry: DynamicModelEntry) -> Model:
    config = DYNAMIC_PROVIDERS[provider]

    # Start with entry values
    max_tokens = entry.max_tokens
    supports_images = entry.supports_images
    supports_thinking = entry.supports_thinking
    context_window = entry.context_window

    from .context_length import context_length_manager

    limits = context_length_manager.get_limits(entry.id)
    is_matched = entry.id in context_length_manager._limits or any(
        entry.id.lower() in k.lower() or k.lower() in entry.id.lower()
        for k in context_length_manager._limits
    )

    if is_matched:
        if context_window is None or context_window == 0:
            context_window = limits.context
        if max_tokens is None or max_tokens == 0:
            max_tokens = limits.output
        if not supports_thinking:
            supports_thinking = limits.supports_reasoning
        if not supports_images:
            supports_images = limits.supports_vision
        if context_window and max_tokens and context_window - max_tokens <= 8192:
            max_tokens = min(16384, context_window)

        supports_tools = limits.supports_tools
        supports_audio = limits.supports_audio
    else:
        if context_window and max_tokens and context_window - max_tokens <= 8192:
            max_tokens = min(16384, context_window)
        supports_tools = True
        supports_audio = False

    return Model(
        id=entry.id,
        provider=provider,
        api=config.api,
        base_url=config.base_url,
        max_tokens=max_tokens,
        supports_images=supports_images,
        supports_thinking=supports_thinking,
        context_window=context_window,
        supports_tools=supports_tools,
        supports_audio=supports_audio,
    )


def get_dynamic_models(force_refresh: bool = False) -> list[Model]:
    """Return all dynamic models converted to the static :class:`Model` shape."""
    out: list[Model] = []
    for provider in DYNAMIC_PROVIDERS:
        for entry in get_provider_models(provider, force_refresh=force_refresh):
            out.append(_to_static_model(provider, entry))
    return out


def find_dynamic_model(model_id: str, provider: str | None = None) -> Model | None:
    """Look up a single model in the dynamic catalog (cache-only, sync)."""
    providers: list[str]
    if provider:
        if provider not in DYNAMIC_PROVIDERS:
            return None
        providers = [provider]
    else:
        providers = list(DYNAMIC_PROVIDERS)

    for name in providers:
        cached = _read_cache(name)
        if cached is None:
            continue
        for entry in cached.models:
            if entry.id == model_id:
                return _to_static_model(name, entry)
    return None


def get_dynamic_provider_headers(provider: str) -> dict[str, str]:
    """Return the default headers a provider requires (e.g. Kilo banner)."""
    config = DYNAMIC_PROVIDERS.get(provider)
    return dict(config.headers) if config else {}


def get_dynamic_model_ids(force_refresh: bool = False) -> dict[str, list[str]]:
    """Return ``{provider: [model_id, ...]}`` for the dynamic catalog."""
    result: dict[str, list[str]] = {}
    for provider in DYNAMIC_PROVIDERS:
        result[provider] = [
            m.id for m in get_provider_models(provider, force_refresh=force_refresh)
        ]
    return result


def get_all_models_with_dynamic(force_refresh: bool = False) -> list[Model]:
    """Return the catalog merged with freshly-fetched dynamic models."""
    from .models import dedupe_models
    from .provider_catalog import get_all_catalog_models

    merged: list[Model] = get_all_catalog_models()
    merged.extend(get_dynamic_models(force_refresh=force_refresh))
    return dedupe_models(merged)


__all__ = [
    "CACHE_DIR_NAME",
    "CACHE_TTL_SECONDS",
    "DYNAMIC_PROVIDERS",
    "FETCH_TIMEOUT_SECONDS",
    "CachedCatalog",
    "DynamicModelEntry",
    "DynamicProviderConfig",
    "aget_provider_models",
    "find_dynamic_model",
    "get_all_models_with_dynamic",
    "get_cache_dir",
    "get_dynamic_model_ids",
    "get_dynamic_models",
    "get_dynamic_provider",
    "get_dynamic_provider_headers",
    "get_provider_models",
    "refresh_all_providers",
    "refresh_provider",
    "register_dynamic_provider",
]
