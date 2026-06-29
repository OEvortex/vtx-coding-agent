from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PIDManager:
    def __init__(self, pid_file: Path | None = None) -> None:
        self._pid_file = pid_file or Path.home() / ".vtx" / "claw.pid"
        self._pid_file.parent.mkdir(parents=True, exist_ok=True)

    def write(self, pid: int) -> None:
        self._pid_file.write_text(str(pid))

    def read(self) -> int | None:
        if self._pid_file.exists():
            try:
                return int(self._pid_file.read_text().strip())
            except Exception:
                return None
        return None

    def clear(self) -> None:
        if self._pid_file.exists():
            self._pid_file.unlink()
