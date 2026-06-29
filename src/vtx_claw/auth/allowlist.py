from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ApprovalManager:
    def __init__(self, allow_file: Path | None = None) -> None:
        self._file = allow_file or Path.home() / ".vtx" / "claw" / "exec_allowlist.json"
        self._allowed: dict[str, list[str]] = {}
        self._load()

    def _load(self) -> None:
        if self._file.exists():
            try:
                import json
                self._allowed = json.loads(self._file.read_text())
            except Exception:
                logger.exception("Failed to load exec allowlist")

    def is_allowed(self, who: str, command: str) -> bool:
        user_allow = self._allowed.get(who, [])
        return "*" in user_allow or command.split()[0] in user_allow

    def add(self, who: str, command: str) -> None:
        self._allowed.setdefault(who, [])
        bin_name = command.split()[0]
        if bin_name not in self._allowed[who]:
            self._allowed[who].append(bin_name)
        self._persist()

    def _persist(self) -> None:
        import json
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(self._allowed, indent=2))
