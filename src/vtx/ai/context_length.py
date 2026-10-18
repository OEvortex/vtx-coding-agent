"""Dynamic context length manager for models.

Fetches model context/output limits from the models.dev API and
caches them. Provides lookup by model ID with fuzzy matching fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

MODELS_DEV_API_URL = "https://models.dev/api.json"
CACHE_FILE = "models_dev_limits.json"
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours
# How often a long-lived process re-attempts a refresh after its cache went
# stale, so a session older than the TTL still picks up new models/efforts.
STALE_RECHECK_SECONDS = 60 * 60


def safe_max_output_tokens(context: int) -> int:
    if context <= 128 * 1024:
        return 8192
    if context <= 256 * 1024:
        return 16384
    return 32768


@dataclass
class TokenLimits:
    context: int
    output: int
    supports_reasoning: bool = False
    supports_vision: bool = False
    supports_tools: bool = False
    supports_audio: bool = False
    thinking_level_map: dict[str, str | None] | None = None


class ContextLengthManager:
    def __init__(self) -> None:
        self._limits: dict[str, TokenLimits] = {}
        self._provider_limits: dict[str, dict[str, TokenLimits]] = {}
        self._loaded = False
        self._lock = threading.Lock()
        # Stale-while-revalidate state. ``_stale`` means "what is loaded is
        # usable but a refresh is due"; it is the difference between serving
        # yesterday's catalog and serving nothing at all.
        self._stale = True
        self._refreshing = False
        self._last_refresh_attempt = 0.0

    def _get_cache_path(self) -> Path:
        from vtx.core.paths import get_config_dir

        return get_config_dir() / CACHE_FILE

    def _load_from_cache(self) -> bool:
        path = self._get_cache_path()
        try:
            if not path.exists():
                return False
            data = json.loads(path.read_text(encoding="utf-8"))
            self._stale = (path.stat().st_mtime + CACHE_TTL_SECONDS) <= time.time()
        except Exception:
            return False
        self._parse_limits(data)
        return True

    def _save_to_cache(self, data: dict[str, Any]) -> None:
        try:
            path = self._get_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("Failed to save models.dev cache: %s", exc)

    def _fetch_and_parse(self) -> None:
        self._last_refresh_attempt = time.time()
        try:
            req = Request(MODELS_DEV_API_URL, headers={"User-Agent": "vtx/1.0"})
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                with self._lock:
                    self._parse_limits(data)
                    self._save_to_cache(data)
                self._stale = False
                logger.info("Loaded model limits from models.dev")
        except (URLError, Exception) as exc:
            logger.debug("Failed to fetch model limits: %s", exc)

    def _parse_limits(self, data: dict[str, Any]) -> None:
        from vtx.ai.thinking import parse_models_dev_reasoning_options

        provider_limits: dict[str, dict[str, TokenLimits]] = {}
        limits_map: dict[str, TokenLimits] = {}

        for provider_name, provider_data in data.items():
            if not isinstance(provider_data, dict):
                continue
            models = provider_data.get("models", {})
            if not models or not isinstance(models, dict):
                continue
            p_key = str(provider_name).strip().lower()
            p_dict = provider_limits.setdefault(p_key, {})

            for model_id, model_info in models.items():
                if not isinstance(model_info, dict):
                    continue
                limit = model_info.get("limit") or {}
                context = limit.get("context", 0) if isinstance(limit, dict) else 0
                output = limit.get("output", 0) if isinstance(limit, dict) else 0
                if context > 0 and context - output <= 8192:
                    output = safe_max_output_tokens(context)

                modalities = model_info.get("modalities", {}) or {}
                input_mods = modalities.get("input", []) if isinstance(modalities, dict) else []
                output_mods = modalities.get("output", []) if isinstance(modalities, dict) else []
                thinking_level_map = parse_models_dev_reasoning_options(
                    model_info.get("reasoning_options"), max_tokens=output or None
                )
                supports_reasoning = bool(model_info.get("reasoning", False))
                if thinking_level_map is not None and not supports_reasoning:
                    supports_reasoning = True

                item = TokenLimits(
                    context=context,
                    output=output,
                    supports_reasoning=supports_reasoning,
                    supports_vision="image" in input_mods,
                    supports_tools=bool(model_info.get("tool_call", False)),
                    supports_audio="audio" in input_mods or "audio" in output_mods,
                    thinking_level_map=thinking_level_map,
                )

                p_dict[model_id] = item
                p_dict[model_id.lower()] = item
                if "/" in model_id:
                    bare_id = model_id.split("/", 1)[1]
                    p_dict.setdefault(bare_id, item)
                    p_dict.setdefault(bare_id.lower(), item)

                existing = limits_map.get(model_id)
                if existing and existing.supports_reasoning and not item.supports_reasoning:
                    pass
                else:
                    limits_map[model_id] = item

                limits_map[f"{p_key}/{model_id}"] = item
                if "/" in model_id:
                    bare = model_id.split("/", 1)[1]
                    if bare not in limits_map or (
                        item.supports_reasoning and not limits_map[bare].supports_reasoning
                    ):
                        limits_map.setdefault(bare, item)

        self._limits.update(limits_map)
        for p, d in provider_limits.items():
            self._provider_limits.setdefault(p, {}).update(d)

    def _schedule_refresh(self) -> None:
        """Fetch models.dev without blocking the caller.

        The TUI resolves limits from the event loop (``_handle_model_command``
        lives there), and a 10s ``urlopen`` on that thread freezes the picker
        and looks like "/model does not work". So inside a running loop the
        fetch runs in a daemon thread and lands in ``_limits`` when it
        finishes; outside a loop there is nothing to block, so stay inline.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._fetch_and_parse()
            return
        if self._refreshing:
            return
        self._refreshing = True
        threading.Thread(
            target=self._refresh_in_background, name="vtx-models-dev", daemon=True
        ).start()

    def _refresh_in_background(self) -> None:
        try:
            self._fetch_and_parse()
        finally:
            self._refreshing = False

    def ensure_loaded(self) -> None:
        if self._loaded:
            self._refresh_if_due()
            return
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            loaded = self._load_from_cache()
        if not loaded or self._stale:
            self._schedule_refresh()

    def _refresh_if_due(self) -> None:
        """Re-check an expired cache on later lookups, rate-limited.

        Without this a process started with an empty cache would call
        ``_schedule_refresh`` once, fail offline, and then never try again for
        as long as it lives - the picker would run on empty limits forever.
        """
        if not self._stale or self._refreshing:
            return
        since = time.time() - self._last_refresh_attempt
        if self._last_refresh_attempt and since < STALE_RECHECK_SECONDS:
            return
        self._schedule_refresh()

    def get_limits(self, model: str, provider: str | None = None) -> TokenLimits:
        self.ensure_loaded()
        if not model:
            return TokenLimits(context=0, output=0)

        # 1) Exact match in provider-scoped mapping if provider given
        p_clean = provider.strip().lower() if provider else None
        if p_clean and p_clean in self._provider_limits:
            p_dict = self._provider_limits[p_clean]
            if model in p_dict:
                return p_dict[model]
            if model.lower() in p_dict:
                return p_dict[model.lower()]
            if "/" in model:
                bare = model.split("/", 1)[1]
                if bare in p_dict:
                    return p_dict[bare]
                if bare.lower() in p_dict:
                    return p_dict[bare.lower()]

        # 2) Exact match in global limits
        if model in self._limits:
            return self._limits[model]
        if model.lower() in self._limits:
            return self._limits[model.lower()]

        if p_clean:
            prefixed = f"{p_clean}/{model}"
            if prefixed in self._limits:
                return self._limits[prefixed]
            if prefixed.lower() in self._limits:
                return self._limits[prefixed.lower()]

        # 3) Check bare model ID (strip org / vendor prefix if present)
        if "/" in model:
            bare = model.split("/", 1)[1]
            if bare in self._limits:
                return self._limits[bare]
            if bare.lower() in self._limits:
                return self._limits[bare.lower()]

        # 4) Check stripped free/preview/latest/instruct suffix
        for suffix in ("-free", "_free", "-preview", "-latest", "-instruct"):
            if model.lower().endswith(suffix):
                stem = model[: -len(suffix)]
                stem_limits = self.get_limits(stem, provider=provider)
                if stem_limits.context > 0 or stem_limits.supports_reasoning:
                    return stem_limits

        # 5) Smart fuzzy matching
        return self._fuzzy_match(model, provider=p_clean)

    def _fuzzy_match(self, model: str, provider: str | None = None) -> TokenLimits:
        def normalize(s: str) -> str:
            return s.lower().replace("-", "").replace("_", "").replace(".", "").replace("/", "")

        target = normalize(model)
        if not target:
            return TokenLimits(context=0, output=0)

        candidates: list[tuple[str, TokenLimits]] = []
        if provider and provider in self._provider_limits:
            candidates.extend(list(self._provider_limits[provider].items()))
        candidates.extend(list(self._limits.items()))

        best_score = 0.0
        best_limits: TokenLimits | None = None

        for cand_id, limits in candidates:
            cand_norm = normalize(cand_id)
            if not cand_norm:
                continue

            score = 0.0
            if cand_norm == target:
                score = 100.0
            elif cand_norm.startswith(target) or target.startswith(cand_norm):
                overlap = min(len(cand_norm), len(target))
                score = 80.0 + (overlap / max(len(cand_norm), len(target))) * 10.0
            elif cand_norm in target or target in cand_norm:
                overlap = min(len(cand_norm), len(target))
                if overlap >= 3:
                    score = 60.0 + (overlap / max(len(cand_norm), len(target))) * 20.0

            if score > 0.0:
                if limits.supports_reasoning or limits.thinking_level_map:
                    score += 5.0
                if score > best_score:
                    best_score = score
                    best_limits = limits

        if best_limits is not None and best_score >= 60.0:
            return best_limits

        return TokenLimits(context=0, output=0)

    def register(self, model: str, context: int, output: int | None = None, **kwargs: Any) -> None:
        limits = TokenLimits(context=context, output=output or 0, **kwargs)
        self._limits[model] = limits
        if "/" in model:
            provider, bare = model.split("/", 1)
            p_dict = self._provider_limits.setdefault(provider.lower(), {})
            p_dict[bare] = limits
            p_dict[model] = limits

    def get_context_length(self, model: str, provider: str | None = None) -> int:
        return self.get_limits(model, provider=provider).context

    def get_max_output(self, model: str, provider: str | None = None) -> int:
        return self.get_limits(model, provider=provider).output

    def supports_reasoning(self, model: str, provider: str | None = None) -> bool:
        return self.get_limits(model, provider=provider).supports_reasoning

    def supports_vision(self, model: str, provider: str | None = None) -> bool:
        return self.get_limits(model, provider=provider).supports_vision

    def supports_tools(self, model: str, provider: str | None = None) -> bool:
        return self.get_limits(model, provider=provider).supports_tools


context_length_manager = ContextLengthManager()
