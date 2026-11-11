"""FreePi (freepi.ai) credentials.

FreePi CLI stores its GitHub-OAuth JWT at ``~/.free-pi/agent/credentials.json``
(``{"token": "eyJ..."}``). VTX reads that file directly — login/logout stays
in the FreePi CLI. ``FREEPI_API_KEY`` env var overrides the file.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

_FREEPI_CLI_DIR = Path.home() / ".free-pi" / "agent"
_CREDENTIALS_PATH = _FREEPI_CLI_DIR / "credentials.json"


@dataclass(frozen=True)
class FreePiCredentials:
    token: str


def _jwt_exp(token: str) -> float | None:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        exp = data.get("exp")
        return float(exp) if isinstance(exp, (int, float)) else None
    except Exception:
        return None


def load_freepi_credentials() -> FreePiCredentials | None:
    env_token = os.environ.get("FREEPI_API_KEY", "").strip()
    token = ""
    if _CREDENTIALS_PATH.exists():
        try:
            data = json.loads(_CREDENTIALS_PATH.read_text(encoding="utf-8"))
            token = str(data.get("token") or "")
        except (OSError, json.JSONDecodeError):
            pass
    if env_token:
        token = env_token
    if not token:
        return None
    return FreePiCredentials(token=token)


def is_freepi_logged_in() -> bool:
    return load_freepi_credentials() is not None


def get_valid_freepi_token_sync() -> str | None:
    creds = load_freepi_credentials()
    if creds is None:
        return None
    exp = _jwt_exp(creds.token)
    if exp is not None and time.time() >= exp - 60:
        return None
    return creds.token
