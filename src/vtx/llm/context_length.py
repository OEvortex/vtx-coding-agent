"""Dynamic context length manager for models.

Fetches model context/output limits from the models.dev API and
caches them. Provides lookup by model ID with fuzzy matching fallback.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

MODELS_DEV_API_URL = "https://models.dev/api.json"
CACHE_FILE = "models_dev_limits.json"
DEFAULT_CONTEXT_LENGTH = 128 * 1024  # 131072
DEFAULT_OUTPUT_TOKENS = 16 * 1024  # 16384
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours


@dataclass
class TokenLimits:
    context: int
    output: int
    supports_reasoning: bool = False
    supports_vision: bool = False
    supports_tools: bool = False
    supports_audio: bool = False


class ContextLengthManager:
    def __init__(self) -> None:
        self._limits: dict[str, TokenLimits] = {}
        self._loaded = False
        self._lock = threading.Lock()

    def _get_cache_path(self) -> Path:
        from ..config import get_config_dir

        return get_config_dir() / CACHE_FILE

    def _load_from_cache(self) -> bool:
        path = self._get_cache_path()
        try:
            import time

            if path.exists() and (path.stat().st_mtime + CACHE_TTL_SECONDS) > time.time():
                data = json.loads(path.read_text(encoding="utf-8"))
                self._parse_limits(data)
                return True
        except Exception:
            pass
        return False

    def _save_to_cache(self, data: dict[str, Any]) -> None:
        try:
            path = self._get_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("Failed to save models.dev cache: %s", exc)

    def _fetch_and_parse(self) -> None:
        try:
            req = Request(MODELS_DEV_API_URL, headers={"User-Agent": "vtx/1.0"})
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self._parse_limits(data)
                self._save_to_cache(data)
                logger.info("Loaded model limits from models.dev")
        except (URLError, Exception) as exc:
            logger.debug("Failed to fetch model limits: %s", exc)

    def _parse_limits(self, data: dict[str, Any]) -> None:
        for _provider_name, provider_data in data.items():
            if not isinstance(provider_data, dict):
                continue
            models = provider_data.get("models", {})
            if not models:
                continue
            for model_id, model_info in models.items():
                limit = model_info.get("limit", {})
                if not limit:
                    continue
                context = limit.get("context", 0)
                output = limit.get("output", DEFAULT_OUTPUT_TOKENS)
                if context > 0:
                    modalities = model_info.get("modalities", {})
                    input_mods = modalities.get("input", [])
                    output_mods = modalities.get("output", [])
                    self._limits[model_id] = TokenLimits(
                        context=context,
                        output=output,
                        supports_reasoning=bool(model_info.get("reasoning", False)),
                        supports_vision="image" in input_mods,
                        supports_tools=bool(model_info.get("tool_call", False)),
                        supports_audio="audio" in input_mods or "audio" in output_mods,
                    )

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            if not self._load_from_cache():
                self._fetch_and_parse()
            self._loaded = True

    def get_limits(self, model: str) -> TokenLimits:
        self.ensure_loaded()

        if model in self._limits:
            return self._limits[model]

        model_lower = model.lower()
        for model_id, limits in self._limits.items():
            if model_lower in model_id.lower() or model_id.lower() in model_lower:
                return limits

        return TokenLimits(context=DEFAULT_CONTEXT_LENGTH, output=DEFAULT_OUTPUT_TOKENS)

    def register(self, model: str, context: int, output: int | None = None, **kwargs: Any) -> None:
        self._limits[model] = TokenLimits(
            context=context, output=output or DEFAULT_OUTPUT_TOKENS, **kwargs
        )

    def get_context_length(self, model: str) -> int:
        return self.get_limits(model).context

    def get_max_output(self, model: str) -> int:
        return self.get_limits(model).output

    def supports_reasoning(self, model: str) -> bool:
        return self.get_limits(model).supports_reasoning

    def supports_vision(self, model: str) -> bool:
        return self.get_limits(model).supports_vision

    def supports_tools(self, model: str) -> bool:
        return self.get_limits(model).supports_tools


context_length_manager = ContextLengthManager()
