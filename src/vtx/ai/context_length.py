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


def _normalize(s: str) -> str:
    return s.lower().replace("-", "").replace("_", "").replace(".", "").replace("/", "")


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
        self._norm_limits: list[tuple[str, TokenLimits]] = []
        self._norm_provider_limits: dict[str, list[tuple[str, TokenLimits]]] = {}
        # Lookup indexes so fuzzy matching does not scan every candidate:
        # normalized-exact hits (the common case) return directly, and the
        # prefix buckets make the fast path O(bucket) instead of O(N).
        self._norm_exact_global: dict[str, TokenLimits] = {}
        self._norm_exact_provider: dict[str, dict[str, TokenLimits]] = {}
        self._norm_prefix: dict[str, list[tuple[str, TokenLimits]]] = {}
        self._norm_suffix: dict[str, list[tuple[str, TokenLimits]]] = {}
        self._norm_provider_prefix: dict[str, dict[str, list[tuple[str, TokenLimits]]]] = {}
        # 2-gram -> candidate indices into ``_norm_limits``, for the
        # contains-match fallback. A candidate can only contain the target if
        # it contains the target's rarest gram, which turns an O(N) scan per
        # miss into a lookup of one posting list.
        self._gram_postings: dict[str, Any] = {}
        self._fuzzy_cache: dict[tuple[str, str | None], TokenLimits] = {}

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

        # Pre-normalized candidate lists so fuzzy matching never re-normalizes.
        self._norm_limits = [(_normalize(mid), lim) for mid, lim in self._limits.items()]
        self._norm_provider_limits = {
            p: [(_normalize(mid), lim) for mid, lim in d.items()]
            for p, d in self._provider_limits.items()
        }
        self._norm_exact_global = {}
        self._norm_exact_provider = {}
        self._norm_prefix = {}
        self._norm_suffix = {}
        self._norm_provider_prefix = {}
        for norm, lim in self._norm_limits:
            self._norm_exact_global.setdefault(norm, lim)
            self._norm_prefix.setdefault(norm[:2], []).append((norm, lim))
            self._norm_suffix.setdefault(norm[-2:], []).append((norm, lim))
        for p, lst in self._norm_provider_limits.items():
            exact: dict[str, TokenLimits] = {}
            prefix: dict[str, list[tuple[str, TokenLimits]]] = {}
            for norm, lim in lst:
                exact.setdefault(norm, lim)
                prefix.setdefault(norm[:2], []).append((norm, lim))
            self._norm_exact_provider[p] = exact
            self._norm_provider_prefix[p] = prefix

        # Postings for the contains-match fallback, global scope only:
        # provider lists are small enough to keep scanning. ``array("i")``
        # keeps ~300k postings at a few MB instead of tens.
        from array import array

        postings: dict[str, Any] = {}
        for idx, (norm, _lim) in enumerate(self._norm_limits):
            if len(norm) < 2:
                continue
            seen: set[str] = set()
            for i in range(len(norm) - 1):
                gram = norm[i : i + 2]
                if gram in seen:
                    continue
                seen.add(gram)
                postings.setdefault(gram, array("i")).append(idx)
        self._gram_postings = postings

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

    def get_limits(
        self, model: str, provider: str | None = None, *, fuzzy: bool = True
    ) -> TokenLimits:
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
                stem_limits = self.get_limits(stem, provider=provider, fuzzy=fuzzy)
                if stem_limits.context > 0 or stem_limits.supports_reasoning:
                    return stem_limits

        # 5) Smart fuzzy matching
        if not fuzzy:
            return TokenLimits(context=0, output=0)
        return self._fuzzy_match(model, provider=p_clean)

    def _fuzzy_match(self, model: str, provider: str | None = None) -> TokenLimits:
        cache_key = (model, provider)
        cached = self._fuzzy_cache.get(cache_key)
        if cached is not None:
            return cached

        target = _normalize(model)
        if not target:
            empty = TokenLimits(context=0, output=0)
            self._fuzzy_cache[cache_key] = empty
            return empty

        result = self._fuzzy_match_uncached(target, provider)
        self._fuzzy_cache[cache_key] = result
        return result

    @staticmethod
    def _exact_bonus(limits: TokenLimits) -> float:
        return 5.0 if (limits.supports_reasoning or limits.thinking_level_map) else 0.0

    def _fuzzy_match_uncached(self, target: str, provider: str | None) -> TokenLimits:
        # Normalized-exact fast path: ``cand_norm == target`` scores 100 (+5
        # reasoning bonus), the maximum any candidate can reach, so the scans
        # below would pick exactly this. Provider scope wins ties because it is
        # scanned first; a reasoning-capable global entry can still outscore a
        # plain provider one, which the adjusted comparison reproduces.
        p_exact = (
            self._norm_exact_provider.get(provider, {}).get(target)
            if provider is not None
            else None
        )
        g_exact = self._norm_exact_global.get(target)
        if p_exact is not None and (
            g_exact is None
            or 100.0 + self._exact_bonus(p_exact) >= 100.0 + self._exact_bonus(g_exact)
        ):
            return p_exact
        if g_exact is not None:
            return g_exact

        # Build the candidate list: provider-scoped first, then global. Each
        # entry pairs the prefix bucket (fast path) with the full list
        # (contains-match fallback).
        scopes: list[
            tuple[dict[str, list[tuple[str, TokenLimits]]], list[tuple[str, TokenLimits]]]
        ] = []
        if provider and provider in self._norm_provider_limits:
            scopes.append(
                (
                    self._norm_provider_prefix.get(provider, {}),
                    self._norm_provider_limits[provider],
                )
            )
        scopes.append((self._norm_prefix, self._norm_limits))

        best_score = 0.0
        best_limits: TokenLimits | None = None

        # Fast path: only candidates sharing the first two normalized chars.
        prefix2 = target[:2]
        for buckets, _full in scopes:
            for cand_norm, limits in buckets.get(prefix2, ()):
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

        # Fallback: contains matches whose first chars differ. Every fallback
        # score needs ``overlap >= 3``, so a target shorter than 3 chars can
        # never match here — skip the scan entirely (same result, no work).
        if best_score < 60.0 and len(target) >= 3:

            def _score_fallback(cand_norm: str, limits: TokenLimits) -> None:
                nonlocal best_score, best_limits
                if not cand_norm or cand_norm[:2] == prefix2:
                    return
                if cand_norm in target or target in cand_norm:
                    overlap = min(len(cand_norm), len(target))
                    if overlap < 3:
                        return
                    score = 60.0 + (overlap / max(len(cand_norm), len(target))) * 20.0
                    if limits.supports_reasoning or limits.thinking_level_map:
                        score += 5.0
                    if score > best_score:
                        best_score = score
                        best_limits = limits

            # Provider-scoped lists are small; scan them whole. The global
            # list (last scope) goes through the indexes below.
            for _buckets, cand_list in scopes[:-1]:
                for cand_norm, limits in cand_list:
                    _score_fallback(cand_norm, limits)

            grams = {target[i : i + 2] for i in range(len(target) - 1)}
            # ``cand_norm in target``: the candidate's opening AND closing
            # grams must both be grams of the target, so only candidates in
            # the intersection of the two bucket unions can match.
            start: dict[int, tuple[str, TokenLimits]] = {}
            end: dict[int, tuple[str, TokenLimits]] = {}
            for gram in grams:
                for pair in self._norm_prefix.get(gram, ()):
                    start[id(pair)] = pair
                for pair in self._norm_suffix.get(gram, ()):
                    end[id(pair)] = pair
            small, large = (start, end) if len(start) <= len(end) else (end, start)
            large_ids = {id(pair) for pair in large.values()}
            for pair in small.values():
                if id(pair) in large_ids:
                    _score_fallback(*pair)
            # ``target in cand_norm``: the candidate contains every target
            # gram, hence the rarest one — one posting list instead of 14k.
            if grams:
                rarest = min(grams, key=lambda g: len(self._gram_postings.get(g, ())))
                for idx in self._gram_postings.get(rarest, ()):
                    cand_norm, limits = self._norm_limits[idx]
                    _score_fallback(cand_norm, limits)

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
