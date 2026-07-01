"""Supercode OAuth — reads the bearer token from the Supercode CLI's auth file.

The Supercode CLI stores its GitHub OAuth session token at
``~/.better-auth/token.json`` after ``supercode login``. This module
reads that token so Vtx can use the Supercode proxy API without
re-authenticating.

This is the single source of truth — no env var fallback.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SupercodeCredentials:
    """Bearer token for the Supercode proxy API."""

    token: str
    expires_at: str | None = None


_SUPERCODE_TOKEN_PATH = Path.home() / ".better-auth" / "token.json"


def get_supercode_auth_path() -> Path:
    return _SUPERCODE_TOKEN_PATH


def load_supercode_credentials() -> SupercodeCredentials | None:
    """Read the Supercode CLI auth file and return the bearer token."""
    path = _SUPERCODE_TOKEN_PATH
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        token = data.get("access_token")
        if not token:
            return None
        return SupercodeCredentials(token=token, expires_at=data.get("expires_at"))
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def is_supercode_logged_in() -> bool:
    return load_supercode_credentials() is not None
