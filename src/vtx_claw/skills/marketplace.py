from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MarketplaceClient:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache = cache_dir or Path.home() / ".vtx" / "claw" / "marketplace"
        self._cache.mkdir(parents=True, exist_ok=True)
        self._catalog_path = self._cache / "catalog.json"

    def search(self, query: str) -> list[dict[str, Any]]:
        catalog = self._read_catalog()
        q = query.lower()
        return [
            s
            for s in catalog
            if q in s.get("name", "").lower() or q in s.get("description", "").lower()
        ]

    def install(self, skill_id: str) -> dict[str, Any]:
        catalog = self._read_catalog()
        for entry in catalog:
            if entry.get("id") == skill_id:
                return {"status": "installed", "skill": entry}
        return {"status": "not_found", "skill_id": skill_id}

    def _read_catalog(self) -> list[dict[str, Any]]:
        if self._catalog_path.exists():
            try:
                return json.loads(self._catalog_path.read_text())
            except Exception:
                logger.exception("Failed to load marketplace catalog")
        return []
