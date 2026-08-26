"""Cline OAuth — WorkOS device flow via api.cline.bot.

Replicates ``@cline/core`` auth (WorkOS ``client_01K3A541FN8TA3EPPHTD2325AR``)
so VTX can use a Cline Pass subscription without an API key.

Flow:
  1. POST https://api.workos.com/user_management/authorize/device
  2. Poll POST https://api.workos.com/user_management/authenticate
  3. POST https://api.cline.bot/api/v1/auth/register  -> Cline JWT (workos:...)
  4. POST https://api.cline.bot/api/v1/auth/refresh   -> refresh

Credentials are stored at ``~/.vtx/cline_auth.json`` (0600).  The native
Cline CLI file ``~/.cline/data/settings/providers.json`` is read as a
fallback so an existing ``cline login`` is reused automatically.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from vtx.core.paths import get_config_dir

_WORKOS_CLIENT_ID = os.getenv("CLINE_WORKOS_CLIENT_ID", "client_01K3A541FN8TA3EPPHTD2325AR")
_WORKOS_BASE = "https://api.workos.com"
_API_BASE = os.getenv("CLINE_API_BASE_URL", "https://api.cline.bot")
_DEVICE_CODE_ENDPOINT = "/user_management/authorize/device"
_DEVICE_TOKEN_ENDPOINT = "/user_management/authenticate"
_REGISTER_ENDPOINT = "/api/v1/auth/register"
_REFRESH_ENDPOINT = "/api/v1/auth/refresh"

_RECOMMENDED_ENDPOINT = "/api/v1/ai/cline/recommended-models"
_RECOMMENDED_CACHE_TTL = 60 * 60 * 6  # 6h

_REFRESH_BUFFER_MS = 60_000
_RETRY_GRACE_MS = 5 * 60_000
_REQUEST_TIMEOUT = 30


@dataclass
class ClineCredentials:
    access: str
    refresh: str
    expires: int
    account_id: str | None = None
    email: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class DeviceCodeResponse:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    interval: int
    expires_in: int


def get_cline_auth_path() -> Path:
    return get_config_dir() / "cline_auth.json"


def get_cline_native_path() -> Path:
    return Path.home() / ".cline" / "data" / "settings" / "providers.json"


def _expires_at_to_ms(value: Any) -> int:
    if isinstance(value, (int, float)):
        # heuristic: seconds vs ms
        return int(value * 1000) if value < 1e12 else int(value)
    if isinstance(value, str):
        try:
            # numeric string
            num = float(value)
            return int(num * 1000) if num < 1e12 else int(num)
        except ValueError:
            pass
        # ISO8601
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except Exception:
            pass
    return 0


def _load_native_credentials() -> ClineCredentials | None:
    path = get_cline_native_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        providers = data.get("providers", {})
        entry = providers.get("cline") or providers.get("cline-pass")
        if not entry:
            return None
        auth = entry.get("settings", {}).get("auth", {})
        access = auth.get("accessToken") or entry.get("settings", {}).get("apiKey") or ""
        refresh = auth.get("refreshToken") or ""
        expires = auth.get("expiresAt") or auth.get("expires") or 0
        if not access or not refresh:
            return None
        expires_ms = _expires_at_to_ms(expires)
        metadata = auth.get("metadata") or {}
        account_id = auth.get("accountId") or metadata.get("userInfo", {}).get("clineUserId")
        email = auth.get("email") or metadata.get("userInfo", {}).get("email")
        return ClineCredentials(
            access=access,
            refresh=refresh,
            expires=expires_ms,
            account_id=account_id,
            email=email,
            metadata=metadata,
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def load_cline_credentials() -> ClineCredentials | None:
    path = get_cline_auth_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ClineCredentials(
                access=data["access"],
                refresh=data["refresh"],
                expires=int(data["expires"]),
                account_id=data.get("account_id"),
                email=data.get("email"),
                metadata=data.get("metadata"),
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            pass
    # fallback to native cline CLI file
    return _load_native_credentials()


def save_cline_credentials(creds: ClineCredentials) -> None:
    path = get_cline_auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "access": creds.access,
        "refresh": creds.refresh,
        "expires": creds.expires,
        "account_id": creds.account_id,
        "email": creds.email,
        "metadata": creds.metadata,
    }
    # strip None
    data = {k: v for k, v in data.items() if v is not None}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def clear_cline_credentials() -> None:
    path = get_cline_auth_path()
    if path.exists():
        with contextlib.suppress(OSError):
            path.unlink()


def is_cline_logged_in() -> bool:
    return load_cline_credentials() is not None


def _needs_refresh(creds: ClineCredentials, buffer_ms: int = _REFRESH_BUFFER_MS) -> bool:
    return (time.time() * 1000) >= (creds.expires - buffer_ms)


def _is_invalid_grant_error(status: int, body: str) -> bool:
    low = body.lower()
    if status in (400, 401, 403) and any(
        k in low for k in ("invalid_grant", "invalid_token", "unauthorized", "expired", "revoked")
    ):
        return True
    return "invalid_grant" in low or "invalid_token" in low


async def _request_device_code() -> DeviceCodeResponse:
    url = f"{_WORKOS_BASE}{_DEVICE_CODE_ENDPOINT}"
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"client_id": _WORKOS_CLIENT_ID},
            timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
        ) as resp,
    ):
        text = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"Cline device authorization failed ({resp.status}): {text}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid WorkOS device response: {exc}") from exc
        if (
            not data.get("device_code")
            or not data.get("user_code")
            or not data.get("verification_uri")
        ):
            raise RuntimeError(f"Invalid WorkOS device authorization response: {text}")
        return DeviceCodeResponse(
            device_code=data["device_code"],
            user_code=data["user_code"],
            verification_uri=data["verification_uri"],
            verification_uri_complete=data.get("verification_uri_complete"),
            interval=int(data.get("interval", 5)),
            expires_in=int(data.get("expires_in", 300)),
        )


async def _poll_workos_token(
    device_code: str, interval: int, expires_in: int, on_poll: Any | None = None
) -> tuple[str, str]:
    url = f"{_WORKOS_BASE}{_DEVICE_TOKEN_ENDPOINT}"
    deadline = time.time() + expires_in
    poll_interval = max(1, interval)
    async with aiohttp.ClientSession() as session:
        while time.time() < deadline:
            if on_poll:
                with contextlib.suppress(Exception):
                    on_poll()
                    pass
            async with session.post(
                url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": _WORKOS_CLIENT_ID,
                },
                timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
            ) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    data = {}
                if resp.status == 200 and data.get("access_token"):
                    access = data["access_token"]
                    refresh = data.get("refresh_token") or ""
                    if not refresh:
                        raise RuntimeError("WorkOS token response missing refresh_token")
                    return access, refresh
                err = data.get("error") if isinstance(data, dict) else None
                if err == "authorization_pending":
                    await asyncio.sleep(poll_interval)
                    continue
                if err == "slow_down":
                    poll_interval += 5
                    await asyncio.sleep(poll_interval)
                    continue
                if err in ("expired_token", "access_denied", "invalid_grant"):
                    raise RuntimeError(f"WorkOS authorization failed: {err}: {text}")
                if resp.status >= 400 and err:
                    raise RuntimeError(f"WorkOS token polling failed ({resp.status}): {text}")
                # unknown transient
                await asyncio.sleep(poll_interval)
        raise TimeoutError("Cline device code flow timed out")


async def _register_cline_token(workos_access: str, workos_refresh: str) -> ClineCredentials:
    url = f"{_API_BASE}{_REGISTER_ENDPOINT}"
    payload = {"accessToken": workos_access, "refreshToken": workos_refresh}
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
        ) as resp,
    ):
        text = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"Cline token registration failed ({resp.status}): {text}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid Cline register response: {exc}") from exc
        # unwrap {success, data: {...}} or direct
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        access = inner.get("accessToken") or inner.get("access_token") or inner.get("access")
        refresh = inner.get("refreshToken") or inner.get("refresh_token") or inner.get("refresh")
        expires_at = inner.get("expiresAt") or inner.get("expires_at") or inner.get("expires")
        user_info = inner.get("userInfo") or inner.get("user_info") or {}
        token_type = inner.get("tokenType") or inner.get("token_type") or "Bearer"
        if not access or not refresh:
            raise RuntimeError(f"Cline register response missing tokens: {text}")
        expires_ms = (
            _expires_at_to_ms(expires_at) if expires_at else int(time.time() * 1000) + 3600 * 1000
        )
        account_id = user_info.get("clineUserId") or user_info.get("cline_user_id")
        email = user_info.get("email")
        metadata = {
            "tokenType": token_type,
            "userInfo": user_info,
            "sessionStartedAtMs": int(time.time() * 1000),
        }
        return ClineCredentials(
            access=access,
            refresh=refresh,
            expires=expires_ms,
            account_id=account_id,
            email=email,
            metadata=metadata,
        )


async def refresh_cline_token(creds: ClineCredentials) -> ClineCredentials:
    url = f"{_API_BASE}{_REFRESH_ENDPOINT}"
    payload = {"refreshToken": creds.refresh, "grantType": "refresh_token"}
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
        ) as resp,
    ):
        text = await resp.text()
        if resp.status >= 400:
            if _is_invalid_grant_error(resp.status, text):
                clear_cline_credentials()
                raise RuntimeError(f"Cline refresh invalid_grant ({resp.status}): {text}")
            raise RuntimeError(f"Cline token refresh failed ({resp.status}): {text}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid Cline refresh response: {exc}") from exc
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        access = inner.get("accessToken") or inner.get("access_token") or inner.get("access")
        refresh = inner.get("refreshToken") or inner.get("refresh_token") or creds.refresh
        expires_at = inner.get("expiresAt") or inner.get("expires_at") or inner.get("expires")
        if not access:
            raise RuntimeError(f"Cline refresh response missing accessToken: {text}")
        expires_ms = (
            _expires_at_to_ms(expires_at) if expires_at else int(time.time() * 1000) + 3600 * 1000
        )
        new_creds = ClineCredentials(
            access=access,
            refresh=refresh,
            expires=expires_ms,
            account_id=inner.get("accountId") or creds.account_id,
            email=inner.get("email") or creds.email,
            metadata=creds.metadata,
        )
        save_cline_credentials(new_creds)
        return new_creds


async def get_valid_cline_credentials() -> ClineCredentials | None:
    creds = load_cline_credentials()
    if not creds:
        return None
    if not _needs_refresh(creds):
        return creds
    # needs refresh
    try:
        return await refresh_cline_token(creds)
    except Exception as exc:
        msg = str(exc).lower()
        if "invalid_grant" in msg or "invalid_token" in msg:
            return None
        # transient failure with grace period -> keep old token
        if creds.expires - int(time.time() * 1000) > _RETRY_GRACE_MS:
            return creds
        raise


async def get_valid_cline_token() -> str | None:
    creds = await get_valid_cline_credentials()
    return creds.access if creds else None


def get_valid_cline_token_sync() -> str | None:
    """Sync fallback for model catalog / provider init (no async refresh).

    Returns a token from env, VTX cache, or native Cline CLI file.  If the
    cached token is expired it is still returned so an async refresh can
    happen on next ``await get_valid_cline_token()`` without blocking.
    """
    env = os.getenv("CLINE_API_KEY", "").strip()
    if env:
        return env
    creds = load_cline_credentials()
    if creds and creds.access:
        return creds.access
    return None


async def start_cline_device_flow() -> DeviceCodeResponse:
    return await _request_device_code()


async def complete_cline_device_flow(
    device_code: str, interval: int, expires_in: int, on_poll: Any | None = None
) -> ClineCredentials:
    workos_access, workos_refresh = await _poll_workos_token(
        device_code, interval, expires_in, on_poll=on_poll
    )
    creds = await _register_cline_token(workos_access, workos_refresh)
    save_cline_credentials(creds)
    return creds


async def login(on_user_code: Any | None = None, on_poll: Any | None = None) -> ClineCredentials:
    device = await _request_device_code()
    if on_user_code:
        with contextlib.suppress(Exception):
            on_user_code(
                device.verification_uri_complete or device.verification_uri, device.user_code
            )
    workos_access, workos_refresh = await _poll_workos_token(
        device.device_code, device.interval, device.expires_in, on_poll=on_poll
    )
    creds = await _register_cline_token(workos_access, workos_refresh)
    save_cline_credentials(creds)
    return creds


# ---------------------------------------------------------------------------
# Free models — separate endpoint /api/v1/ai/cline/recommended-models
# ---------------------------------------------------------------------------


def _cline_free_cache_path() -> Path:
    from vtx.core.paths import get_config_dir

    env = os.getenv("VTX_MODELS_CACHE_DIR")
    base = Path(env) if env else get_config_dir() / "models"
    return base / "cline_free.json"


def _read_cline_free_cache() -> tuple[set[str], float] | None:
    p = _cline_free_cache_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ids = set(data.get("free_ids", []))
        fetched_at = float(data.get("fetched_at", 0))
        if time.time() - fetched_at > _RECOMMENDED_CACHE_TTL:
            return None
        return ids, fetched_at
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _write_cline_free_cache(ids: set[str]) -> None:
    p = _cline_free_cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"free_ids": sorted(ids), "fetched_at": time.time()}, indent=2),
            encoding="utf-8",
        )
        with contextlib.suppress(OSError):
            tmp.chmod(0o600)
        tmp.replace(p)
        with contextlib.suppress(OSError):
            p.chmod(0o600)
    except OSError:
        pass


async def fetch_cline_free_model_ids() -> set[str]:
    """Fetch free model ids from the recommended endpoint (async)."""
    tok = get_valid_cline_token_sync()
    headers = {"Accept": "application/json"}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    url = f"{_API_BASE}{_RECOMMENDED_ENDPOINT}"
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
            ) as resp,
        ):
            if resp.status >= 400:
                return set()
            data = await resp.json()
    except Exception:
        return set()
    free = data.get("free", []) if isinstance(data, dict) else []
    ids: set[str] = set()
    for entry in free:
        if isinstance(entry, dict):
            mid = entry.get("id")
            if isinstance(mid, str) and mid:
                ids.add(mid)
    if ids:
        _write_cline_free_cache(ids)
    return ids


def fetch_cline_free_model_ids_sync() -> set[str]:
    """Sync fetch of free ids (httpx)."""
    import httpx

    tok = get_valid_cline_token_sync()
    headers = {"Accept": "application/json"}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    url = f"{_API_BASE}{_RECOMMENDED_ENDPOINT}"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code >= 400:
                return set()
            data = resp.json()
    except Exception:
        return set()
    free = data.get("free", []) if isinstance(data, dict) else []
    ids: set[str] = set()
    for entry in free:
        if isinstance(entry, dict):
            mid = entry.get("id")
            if isinstance(mid, str) and mid:
                ids.add(mid)
    if ids:
        _write_cline_free_cache(ids)
    return ids


def get_cline_free_model_ids() -> set[str]:
    """Return cached free ids, fetching if stale. Never raises."""
    cached = _read_cline_free_cache()
    if cached is not None:
        ids, _ = cached
        return ids
    # try sync fetch, fall back to empty
    try:
        ids = fetch_cline_free_model_ids_sync()
        if ids:
            return ids
    except Exception:
        pass
    # last resort: read stale cache even if expired
    p = _cline_free_cache_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return set(data.get("free_ids", []))
        except Exception:
            pass
    return set()
