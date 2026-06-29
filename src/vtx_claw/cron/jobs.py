from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class JobResult:
    job_name: str
    success: bool
    output: str = ""
    error: str = ""
    metadata: dict[str, Any] | None = None
