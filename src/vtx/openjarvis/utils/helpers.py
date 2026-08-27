from __future__ import annotations

import datetime
import secrets
from pathlib import Path


def short_id(nbytes: int = 4) -> str:
    return secrets.token_hex(nbytes)


def utc_now_iso() -> str:
    return datetime.datetime.now(tz=datetime.UTC).isoformat()


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p
