"""Auto-fetch model lists from provider /models endpoints.

When a provider has ``fetch_models: true`` in provider.yaml, this module
can fetch ``<base_url>/models``, parse the response, and cache the result
to ``~/.vtx/models/<provider>.json`` with a configurable TTL.

Fetching is opt-in: call ``refresh_provider_models(slug)`` or
``refresh_all_provider_models()`` to trigger a fetch. The catalog
only reads from cache, never blocks on network I/O.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from .models import ApiType, Model

logger = logging.getLogger(__name__)

CACHE_DIR = "models"
DEFAULT_COOLDOWN = 60  # minutes


@dataclass
class FetchedModel:
    id: str
    name: str
    context_length: int = 0
    max_output_tokens: int = 0
    supports_images: bool = False
    api_model_id: str = ""


def _get_cache_dir() -> Path:
    from ..config import get_config_dir

    env = os.environ.get("VTX_MODELS_CACHE_DIR")
    return Path(env) if env else get_config_dir() / CACHE_DIR


def _cache_path(provider_slug: str) -> Path:
    return _get_cache_dir() / f"{provider_slug}.json"


def _read_cache(provider_slug: str) -> list[FetchedModel] | None:
    path = _cache_path(provider_slug)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    fetched_at = data.get("fetched_at", 0)
    cooldown = data.get("cooldown_minutes", DEFAULT_COOLDOWN) * 60
    if time.time() - fetched_at > cooldown:
        return None

    models = []
    for entry in data.get("models", []):
        models.append(
            FetchedModel(
                id=entry["id"],
                name=entry.get("name", entry["id"]),
                context_length=entry.get("context_length", 0),
                max_output_tokens=entry.get("max_output_tokens", 0),
                supports_images=entry.get("supports_images", False),
                api_model_id=entry.get("api_model_id", ""),
            )
        )
    return models


def _write_cache(provider_slug: str, models: list[FetchedModel], cooldown_minutes: int) -> None:
    path = _cache_path(provider_slug)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "fetched_at": time.time(),
            "cooldown_minutes": cooldown_minutes,
            "models": [asdict(m) for m in models],
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("Failed to write model cache for %s: %s", provider_slug, exc)


def _resolve_api_key(provider) -> str | None:
    if provider.api_key_env:
        key = os.environ.get(provider.api_key_env)
        if key:
            return key
    if provider.api_key_optional:
        return "vtx-local"
    return None


def _is_free_model(raw: dict[str, Any]) -> bool:
    bp = raw.get("benchmark_pricing")
    if not isinstance(bp, dict):
        return False
    return bp.get("input_per_mtok_min", 1) == 0 and bp.get("output_per_mtok_min", 1) == 0


def _parse_models(raw_models: list[dict[str, Any]], parser_config) -> list[FetchedModel]:
    models: list[FetchedModel] = []
    for raw in raw_models:
        model_id = raw.get(parser_config.id_field, "")
        if not model_id:
            continue

        api_model_id = ""
        if _is_free_model(raw):
            api_model_id = model_id
            model_id = f"{model_id}-free"

        name = raw.get(parser_config.name_field, model_id)
        if isinstance(name, str) and ":" in name:
            candidate = name.split(":", 1)[1].strip()
            if candidate:
                name = candidate

        context_length = 0
        ctx_val = raw.get(parser_config.context_field)
        if ctx_val is not None:
            with suppress(TypeError, ValueError):
                context_length = int(ctx_val)

        max_output = 0
        out_val = raw.get(parser_config.output_field)
        if out_val is not None:
            with suppress(TypeError, ValueError):
                max_output = int(out_val)

        supports_images = False
        arch = raw.get("architecture")
        if isinstance(arch, dict):
            modalities = arch.get("input_modalities", [])
            supports_images = "image" in modalities if isinstance(modalities, list) else False
        if not supports_images:
            modalities = raw.get("input_modalities")
            supports_images = isinstance(modalities, list) and "image" in modalities

        models.append(
            FetchedModel(
                id=model_id,
                name=name,
                context_length=context_length,
                max_output_tokens=max_output,
                supports_images=supports_images,
                api_model_id=api_model_id,
            )
        )

    return models


def _raw_model_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [m for m in payload if isinstance(m, dict)]
    if isinstance(payload, dict):
        for key in ("data", "models", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                return [m for m in val if isinstance(m, dict)]
    return []


def _fetch_models_sync(provider) -> list[FetchedModel]:
    """Fetch models from a provider's /models endpoint (sync, with network)."""
    if not provider.fetch_models or not provider.base_url:
        return []

    api_key = _resolve_api_key(provider)
    base = provider.base_url.rstrip("/")
    url = f"{base}{provider.models_endpoint}"

    headers = {"Accept": "application/json"}
    if api_key and api_key != "vtx-local":
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code >= 400:
                logger.debug("Failed to fetch models for %s: %s", provider.slug, resp.status_code)
                return []
            payload = resp.json()
    except Exception as exc:
        logger.debug("Error fetching models for %s: %s", provider.slug, exc)
        return []

    raw_models = _raw_model_list(payload)
    if not raw_models:
        return []

    models = _parse_models(raw_models, provider.model_parser)
    cooldown = provider.model_parser.cooldown_minutes
    _write_cache(provider.slug, models, cooldown)
    return models


def refresh_provider_models(slug: str) -> int:
    """Force-refresh a single provider's model cache. Returns model count."""
    from .provider_catalog import get

    provider = get(slug)
    if provider is None or not provider.fetch_models:
        return 0
    models = _fetch_models_sync(provider)
    return len(models)


def refresh_all_provider_models() -> dict[str, int]:
    """Force-refresh all providers. Returns {slug: model_count}."""
    from .provider_catalog import list_providers

    results: dict[str, int] = {}
    for p in list_providers():
        if not p.fetch_models:
            continue
        models = _fetch_models_sync(p)
        results[p.slug] = len(models)
    return results


def get_fetched_models(provider) -> list[Model]:
    """Get fetched models from cache only (no network). Returns empty if not cached."""
    if not provider.fetch_models:
        return []

    cached = _read_cache(provider.slug)
    if cached is None:
        return []

    family_to_api = {
        "openai_compat": ApiType(ApiType.OPENAI_SDK),
        "anthropic": ApiType(ApiType.ANTHROPIC),
    }

    from .context_length import context_length_manager

    models: list[Model] = []
    for entry in cached:
        limits = context_length_manager.get_limits(entry.id)
        is_matched = entry.id in context_length_manager._limits or any(
            entry.id.lower() in k.lower() or k.lower() in entry.id.lower()
            for k in context_length_manager._limits
        )

        max_tokens = entry.max_output_tokens or provider.max_tokens
        supports_images = entry.supports_images or provider.supports_vision
        supports_thinking = provider.supports_thinking
        context_window = entry.context_length or None

        if is_matched:
            if context_window is None or context_window == 0:
                context_window = limits.context
            if max_tokens == 0 or max_tokens == provider.max_tokens:
                max_tokens = limits.output
            if not supports_thinking:
                supports_thinking = limits.supports_reasoning
            if not supports_images:
                supports_images = limits.supports_vision
            supports_tools = limits.supports_tools
            supports_audio = limits.supports_audio
        else:
            supports_tools = provider.supports_tools
            supports_audio = False

        models.append(
            Model(
                id=entry.id,
                provider=provider.slug,
                api=family_to_api[provider.family],
                base_url=provider.base_url or "",
                max_tokens=max_tokens,
                supports_images=supports_images,
                supports_thinking=supports_thinking,
                context_window=context_window,
                supports_tools=supports_tools,
                supports_audio=supports_audio,
                api_model_id=entry.api_model_id,
            )
        )
    return models
