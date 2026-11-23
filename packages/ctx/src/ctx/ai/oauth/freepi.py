"""FreePi (freepi.ai) credentials.

FreePi CLI stores its GitHub-OAuth JWT at ``~/.free-pi/agent/credentials.json``
(``{"token": "eyJ..."}``). CTX reads that file directly — login/logout stays
in the FreePi CLI. ``FREEPI_API_KEY`` env var overrides the file.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp

_FREEPI_CLI_DIR = Path.home() / ".free-pi" / "agent"
_CREDENTIALS_PATH = _FREEPI_CLI_DIR / "credentials.json"
_ADS_TTL_S = 600.0
_ads_last: dict[str, float] = {}


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


async def ensure_freepi_ad_impression(session_id: str = "ctx") -> None:
    """Register one banner ad impression for ``session_id``.

    FreePi gates free completions on ad engagement: the server returns
    ``403 ads_required`` until the session has fetched ``/ads/next``, fired the
    Gravity ``imp_url``, and posted ``/ads/impression``. Cached for 10 minutes
    per session, matching the CLI's banner refresh cadence. Never raises.
    """
    now = time.monotonic()
    if now - _ads_last.get(session_id, 0.0) < _ADS_TTL_S:
        return
    token = get_valid_freepi_token_sync()
    if not token:
        return
    base = os.environ.get("FREEPI_BASE_URL", "https://api.freepi.ai").rstrip("/")
    headers = {"Authorization": f"Bearer {token}", "x-session-id": session_id}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0)) as client:
            async with client.get(
                f"{base}/ads/next?slot=banner", headers=headers, allow_redirects=False
            ) as res:
                if res.status != 200:
                    return
                ad = await res.json(content_type=None)
            imp_url = ad.get("imp_url")
            if imp_url:
                with contextlib.suppress(aiohttp.ClientError, asyncio.TimeoutError):
                    async with client.get(
                        imp_url, timeout=aiohttp.ClientTimeout(total=5.0), allow_redirects=False
                    ) as imp_res:
                        await imp_res.read()
            async with client.post(
                f"{base}/ads/impression",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "ad_id": ad.get("ad_id"),
                    "click_token": ad.get("click_token"),
                    "imp_fired": True,
                },
            ) as imp_res:
                await imp_res.read()
            _ads_last[session_id] = now
    except Exception:
        pass
