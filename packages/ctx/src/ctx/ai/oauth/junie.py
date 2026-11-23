"""Junie CLI (JetBrains Junie Lite) credentials.

Junie CLI authenticates against a JetBrains Account (JBA) OAuth2 server and
exchanges the resulting ``id_token`` for a short-lived "grazie" JWT that the
ingrazzio inference gateway accepts as a Bearer token.  The free tier
(``grazie.individual.lite`` license) unlocks two models:

* ``Qwen/Qwen3.6-27B-FP8``  (vLLM route, ``/llm/vllm/v1``)
* ``gpt-6-luna``            (OpenAI route, ``/llm/openai/v1``)

Credential sources, in priority order:

1. ``JUNIE_API_KEY`` env var - a ready-made grazie JWT (skips all exchange).
2. CTX cache ``~/.ctx/junie_auth.json`` - refreshed JBA tokens + cached grazie JWT.
3. Native Junie CLI session - the refresh token lives in the OS secret service
   (gnome-keyring item ``junie-cli:jb-account-stored`` on Linux).  We import it
   lazily: the first refresh writes the rotated tokens into the CTX cache so the
   keyring is not written back.

Login (``junie_login``) mirrors Junie's own JBA OAuth flow: authorization code +
PKCE (``client_id=junie-cli``, scope ``offline_access openid jb-authn-service``)
with a localhost callback.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import secrets
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from ctx.core.paths import get_config_dir

_JBA_BASE = "https://oauth.account.jetbrains.com"
_AUTHORIZE_URL = f"{_JBA_BASE}/oauth2/auth"
_TOKEN_URL = f"{_JBA_BASE}/oauth2/token"
_CLIENT_ID = "junie-cli"
_SCOPE = "offline_access openid jb-authn-service"

_GRAZIE_BASE = "https://api.jetbrains.ai"
_LICENSE_OBTAIN = "/auth/jetbrains-jwt/license/obtain/grazie-lite"
_LICENSE_EXCHANGE = "/auth/jetbrains-jwt/provide-access/license/v2"
# The lite license id is stable across accounts (auto-granted on first use).
_LITE_LICENSE_ID = "1IS21I31NU"

_INGRAZZIO_BASE = "https://ingrazzio-cloud-prod.labs.jb.gg"
_INGRAZZIO_MODELS_URL = f"{_INGRAZZIO_BASE}/llm/v1/models"

_REQUEST_TIMEOUT = 30
_REFRESH_BUFFER_S = 300  # refresh JBA access token 5 min early

# Secret-service coordinates used by Junie's LinuxSecretServiceStorage.
_SECRET_SERVICE = "junie-cli"
_SECRET_ACCOUNT = (
    "bc1e8660744d7c12a502162a3f7f45043270a8e636d84e4ffcec1d7d223960fcjb-account-stored"
)


@dataclass
class JunieCredentials:
    """JBA OAuth tokens plus the exchanged grazie inference JWT."""

    refresh: str
    access: str
    id_token: str
    expires_at: float  # epoch seconds for the JBA access token
    grazie_token: str = ""
    grazie_expires_at: float = 0.0
    license_id: str = ""
    account_id: str | None = None
    email: str | None = None


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def _decode_jwt_payload(token: str) -> dict[str, Any] | None:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode()))
    except Exception:
        return None


def _jwt_exp(token: str) -> float | None:
    payload = _decode_jwt_payload(token)
    if not payload:
        return None
    exp = payload.get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def get_junie_auth_path() -> Path:
    return get_config_dir() / "junie_auth.json"


def get_junie_native_path() -> Path:
    """Junie CLI's own credential file (BYOK settings live here too)."""
    return Path.home() / ".junie" / "secure_credentials.json"


def _creds_from_dict(data: dict[str, Any]) -> JunieCredentials | None:
    refresh = str(data.get("refresh") or "")
    access = str(data.get("access") or "")
    id_token = str(data.get("id_token") or "")
    if not refresh and not id_token and not access:
        return None
    grazie = str(data.get("grazie_token") or "")
    return JunieCredentials(
        refresh=refresh,
        access=access,
        id_token=id_token,
        expires_at=float(data.get("expires_at") or 0.0),
        grazie_token=grazie,
        grazie_expires_at=float(data.get("grazie_expires_at") or 0.0),
        license_id=str(data.get("license_id") or ""),
        account_id=data.get("account_id"),
        email=data.get("email"),
    )


def load_junie_credentials() -> JunieCredentials | None:
    """Load CTX-cached credentials, falling back to the Junie CLI keyring."""
    env_key = os.environ.get("JUNIE_API_KEY", "").strip()
    path = get_junie_auth_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            creds = _creds_from_dict(data)
            if creds is not None:
                if env_key:
                    creds.grazie_token = env_key
                    creds.grazie_expires_at = _jwt_exp(env_key) or 0.0
                return creds
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    native = _load_native_credentials()
    if native is not None:
        if env_key:
            native.grazie_token = env_key
            native.grazie_expires_at = _jwt_exp(env_key) or 0.0
        return native
    if env_key:
        return JunieCredentials(
            refresh="",
            access="",
            id_token="",
            expires_at=0.0,
            grazie_token=env_key,
            grazie_expires_at=_jwt_exp(env_key) or 0.0,
        )
    return None


def save_junie_credentials(creds: JunieCredentials) -> None:
    path = get_junie_auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "refresh": creds.refresh,
        "access": creds.access,
        "id_token": creds.id_token,
        "expires_at": creds.expires_at,
        "grazie_token": creds.grazie_token,
        "grazie_expires_at": creds.grazie_expires_at,
        "license_id": creds.license_id,
        "account_id": creds.account_id,
        "email": creds.email,
    }
    data = {k: v for k, v in data.items() if v not in (None, "", 0, 0.0)}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def clear_junie_credentials() -> None:
    path = get_junie_auth_path()
    if path.exists():
        with contextlib.suppress(OSError):
            path.unlink()


def is_junie_logged_in() -> bool:
    return load_junie_credentials() is not None


# ---------------------------------------------------------------------------
# Native Junie CLI credentials (OS secret service)
# ---------------------------------------------------------------------------


def _secret_tool_lookup() -> dict[str, Any] | None:
    """Read the ``jb-account-stored`` secret via ``secret-tool`` (sync)."""
    try:
        out = subprocess.run(
            ["secret-tool", "lookup", "service", _SECRET_SERVICE, "account", _SECRET_ACCOUNT],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    jb = data.get("jbAccount") if isinstance(data, dict) else None
    return jb if isinstance(jb, dict) else None


def _load_native_credentials() -> JunieCredentials | None:
    jb = _secret_tool_lookup()
    if not jb:
        return None
    refresh = str(jb.get("refresh_token") or "")
    access = str(jb.get("access_token") or "")
    id_token = str(jb.get("id_token") or "")
    if not refresh and not id_token:
        return None
    return JunieCredentials(
        refresh=refresh,
        access=access,
        id_token=id_token,
        expires_at=0.0,  # refresh on first use; the refresh token is durable
        email=jb.get("email"),
        account_id=str(jb.get("id") or "") or None,
    )


# ---------------------------------------------------------------------------
# Token refresh / exchange
# ---------------------------------------------------------------------------


async def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    import aiohttp

    async with (
        aiohttp.ClientSession() as session,
        session.post(
            url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
        ) as resp,
    ):
        text = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"Junie token request failed ({resp.status}): {text[:300]}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid Junie token response: {text[:200]}") from exc


async def _post_json(
    url: str, token: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    import aiohttp

    async with (
        aiohttp.ClientSession() as session,
        session.post(
            url,
            json=payload if payload is not None else {},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
        ) as resp,
    ):
        text = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"Junie auth request failed ({resp.status}): {text[:300]}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid Junie auth response: {text[:200]}") from exc


async def _refresh_jba(creds: JunieCredentials) -> JunieCredentials:
    data = await _post_form(
        _TOKEN_URL,
        {"grant_type": "refresh_token", "refresh_token": creds.refresh, "client_id": _CLIENT_ID},
    )
    access = str(data.get("access_token") or "")
    refresh = str(data.get("refresh_token") or creds.refresh)
    id_token = str(data.get("id_token") or creds.id_token)
    if not access and not id_token:
        raise RuntimeError("Junie refresh response missing tokens")
    expires_in = data.get("expires_in")
    expires_at = (
        time.time() + float(expires_in)
        if isinstance(expires_in, (int, float))
        else time.time() + 3600
    )
    payload = _decode_jwt_payload(id_token) or {}
    auth = (
        payload.get("https://api.jetbrains.com/auth")
        or payload.get("https://api.jetbrains.ai/auth")
        or {}
    )
    account_id = auth.get("account_id") or creds.account_id
    email = payload.get("email") or creds.email
    return JunieCredentials(
        refresh=refresh,
        access=access,
        id_token=id_token,
        expires_at=expires_at,
        grazie_token=creds.grazie_token,
        grazie_expires_at=creds.grazie_expires_at,
        license_id=creds.license_id,
        account_id=account_id,
        email=email,
    )


async def _exchange_grazie(creds: JunieCredentials) -> tuple[str, float, str]:
    """Exchange the JBA id_token for a grazie inference JWT.

    Returns (token, expires_at, license_id).
    """
    if not creds.id_token:
        raise RuntimeError("Junie credentials missing id_token")
    # Ensure the lite license is granted (idempotent).
    with contextlib.suppress(Exception):
        await _post_json(f"{_GRAZIE_BASE}{_LICENSE_OBTAIN}", creds.id_token)
    data = await _post_json(
        f"{_GRAZIE_BASE}{_LICENSE_EXCHANGE}",
        creds.id_token,
        {"licenseId": creds.license_id or _LITE_LICENSE_ID},
    )
    token = str(data.get("token") or "")
    if not token:
        raise RuntimeError(f"Junie license exchange returned no token: {data}")
    exp = _jwt_exp(token) or (time.time() + 3600)
    return token, exp, str(data.get("licenseId") or creds.license_id or _LITE_LICENSE_ID)


async def get_valid_junie_credentials() -> JunieCredentials | None:
    """Return credentials with a fresh grazie token, refreshing as needed."""
    creds = load_junie_credentials()
    if creds is None:
        return None

    env_key = os.environ.get("JUNIE_API_KEY", "").strip()
    if env_key:
        creds.grazie_token = env_key
        creds.grazie_expires_at = _jwt_exp(env_key) or 0.0
        return creds

    # Need a usable JBA id_token for the exchange.
    if not creds.id_token or creds.expires_at <= time.time() + _REFRESH_BUFFER_S:
        if not creds.refresh:
            return None
        try:
            creds = await _refresh_jba(creds)
        except Exception:
            return None

    # Need a fresh grazie token.
    if not creds.grazie_token or creds.grazie_expires_at <= time.time() + _REFRESH_BUFFER_S:
        try:
            token, exp, license_id = await _exchange_grazie(creds)
        except Exception:
            return None
        creds.grazie_token = token
        creds.grazie_expires_at = exp
        creds.license_id = license_id

    save_junie_credentials(creds)
    return creds


async def get_valid_junie_token() -> str | None:
    creds = await get_valid_junie_credentials()
    return creds.grazie_token if creds else None


def get_valid_junie_token_sync() -> str | None:
    """Sync token getter for model catalog / provider init.

    Returns the cached grazie token if present (even if near expiry - the async
    path refreshes on next request).  Returns ``None`` until the first async
    refresh lands a grazie JWT in the CTX cache.
    """
    env_key = os.environ.get("JUNIE_API_KEY", "").strip()
    if env_key:
        return env_key
    creds = load_junie_credentials()
    if creds and creds.grazie_token:
        return creds.grazie_token
    return None


# ---------------------------------------------------------------------------
# Login (JBA OAuth2 authorization-code + PKCE)
# ---------------------------------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


async def _exchange_code(
    code: str, verifier: str, redirect_uri: str
) -> tuple[str, str, str, float]:
    """Exchange an authorization code for (access, refresh, id_token, expires_at)."""
    data = await _post_form(
        _TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": _CLIENT_ID,
            "code_verifier": verifier,
        },
    )
    access = str(data.get("access_token") or "")
    refresh = str(data.get("refresh_token") or "")
    id_token = str(data.get("id_token") or "")
    if not refresh or not id_token:
        raise RuntimeError("Junie login response missing tokens")
    expires_in = data.get("expires_in")
    expires_at = (
        time.time() + float(expires_in)
        if isinstance(expires_in, (int, float))
        else time.time() + 3600
    )
    return access, refresh, id_token, expires_at


async def login(on_auth_url: Any | None = None) -> JunieCredentials:
    """Run the JBA OAuth flow and store credentials.

    ``on_auth_url(url)`` is called with the authorize URL so the caller can
    open a browser.  A localhost callback server receives the redirect.
    """
    from http.server import BaseHTTPRequestHandler, HTTPServer

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}
    done = asyncio.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            code = params.get("code", [None])[0]
            st = params.get("state", [None])[0]
            if code and st == state:
                result["code"] = code
                done.set()
                body = (
                    b"<html><body><h2>Junie login complete</h2><p>Return to ctx.</p></body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    redirect_uri = f"http://localhost:{port}/callback"

    params = {
        "client_id": _CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": _SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{_AUTHORIZE_URL}?{urlencode(params)}"

    loop = asyncio.get_running_loop()
    serve_task = loop.run_in_executor(None, server.handle_request)

    if on_auth_url:
        with contextlib.suppress(Exception):
            on_auth_url(auth_url)

    try:
        await asyncio.wait_for(done.wait(), timeout=300)
    except TimeoutError:
        server.server_close()
        raise TimeoutError("Junie OAuth login timed out") from None
    await serve_task
    server.server_close()

    code = result.get("code")
    if not code:
        raise RuntimeError("Junie OAuth login missing authorization code")

    access, refresh, id_token, expires_at = await _exchange_code(code, verifier, redirect_uri)
    creds = JunieCredentials(
        refresh=refresh, access=access, id_token=id_token, expires_at=expires_at
    )
    # Exchange for a grazie token immediately so the provider works right away.
    try:
        token, exp, license_id = await _exchange_grazie(creds)
        creds.grazie_token = token
        creds.grazie_expires_at = exp
        creds.license_id = license_id
    except Exception:
        pass
    save_junie_credentials(creds)
    return creds


async def logout() -> None:
    clear_junie_credentials()


# ---------------------------------------------------------------------------
# Model catalog (ingrazzio /llm/v1/models)
# ---------------------------------------------------------------------------

# Only OpenAI-chat-shaped APIs are exposed to ctx; the ingrazzio catalog also
# lists Anthropic/XAI/Vertex/Bedrock protocols that need different wire formats.
_CHAT_APIS = {"OPENAI_COMPLETIONS", "VLLM_COMPLETIONS"}
_PROTOCOLS = {"OPENAI", "VLLM"}

# The catalog lists every model the gateway *can* route, but the free lite
# license only unlocks a couple of them — the rest return 477 "Out of quota".
# Probe each chat model once with a 1-token request and cache the verdict.
_PROBE_CACHE = "junie_probe.json"
_PROBE_TTL_S = 24 * 3600
_PROBE_MAX_TOKENS = 8


def _probe_cache_path() -> Path:
    from ctx.ai.model_fetcher import _get_cache_dir

    return _get_cache_dir() / _PROBE_CACHE


def _read_probe_cache() -> dict[str, Any]:
    try:
        data = json.loads(_probe_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if time.time() - float(data.get("fetched_at", 0)) > _PROBE_TTL_S:
        return {}
    results = data.get("results")
    return results if isinstance(results, dict) else {}


def _write_probe_cache(results: dict[str, Any]) -> None:
    path = _probe_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"fetched_at": time.time(), "results": results}), encoding="utf-8"
        )
    except OSError:
        pass


async def _probe_model(session: Any, model: dict[str, Any], token: str) -> tuple[str, bool]:
    """Return (snapshot, usable) for one catalog entry."""
    import aiohttp

    snapshot = model["snapshot"]
    url = model["route_url"]
    if url.endswith("/responses"):
        body: dict[str, Any] = {
            "model": snapshot,
            "input": "hi",
            "max_output_tokens": 16,
            "store": False,
        }
    else:
        body = {
            "model": snapshot,
            "messages": [{"role": "user", "content": "hi"}],
            "max_completion_tokens": _PROBE_MAX_TOKENS,
            "stream": False,
        }
    try:
        async with session.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            await resp.read()
            return snapshot, resp.status == 200
    except Exception:
        return snapshot, False


async def _filter_free_models(models: list[dict[str, Any]], token: str) -> list[dict[str, Any]]:
    """Probe the catalog and keep only models the lite license can run."""
    import aiohttp

    cached = _read_probe_cache()
    todo = [m for m in models if m["snapshot"] not in cached]
    results = dict(cached)
    if todo:
        async with aiohttp.ClientSession() as session:
            verdicts = await asyncio.gather(*(_probe_model(session, m, token) for m in todo))
        for snapshot, ok in verdicts:
            results[snapshot] = ok
        _write_probe_cache(results)
    return [m for m in models if results.get(m["snapshot"]) is True]


def _catalog_to_models(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for proto in payload.get("protocols", []):
        protocol = str(proto.get("protocol") or "")
        if protocol not in _PROTOCOLS:
            continue
        base_path = str(proto.get("basePath") or "")
        for model in proto.get("models", []):
            apis = set(model.get("apis") or [])
            if not (apis & _CHAT_APIS):
                continue
            snapshot = str(model.get("snapshot") or "")
            if not snapshot:
                continue
            # gpt-6-luna rejects function tools with reasoning_effort on
            # /chat/completions; the Responses API accepts both, so route the
            # OPENAI protocol there.  VLLM (Qwen) has no /responses endpoint.
            if "OPENAI_RESPONSES" in apis:
                route_path = "responses"
                api = "openai-responses"
            else:
                route_path = "chat/completions"
                # VLLM has no /responses; force the chat-completions transport
                # even though the provider default is openai-responses.
                api = "openai-sdk"
            out.append(
                {
                    "id": snapshot,
                    "name": str(model.get("displayName") or snapshot),
                    "snapshot": snapshot,
                    "route_url": f"{_INGRAZZIO_BASE}/{base_path}/{route_path}",
                    "api": api,
                    "is_free": True,  # lite license only exposes free models here
                    "protocol": protocol,
                }
            )
    return out


async def list_junie_models() -> list[dict[str, Any]]:
    """Fetch the ingrazzio catalog and return chat-capable models."""
    import aiohttp

    token = await get_valid_junie_token()
    if not token:
        return []
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    async with (
        aiohttp.ClientSession() as session,
        session.get(
            _INGRAZZIO_MODELS_URL,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
        ) as resp,
    ):
        if resp.status != 200:
            return []
        payload = await resp.json(content_type=None)
    models = _catalog_to_models(payload)
    return await _filter_free_models(models, token)


def list_junie_models_sync() -> list[dict[str, Any]]:
    """Sync wrapper for the model catalog fetcher."""
    try:
        return asyncio.run(list_junie_models())
    except RuntimeError:
        # Already inside an event loop - use the cached grazie token.
        token = get_valid_junie_token_sync()
        if not token:
            return []
        import urllib.request

        req = urllib.request.Request(
            _INGRAZZIO_MODELS_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                payload = json.loads(resp.read())
        except Exception:
            return []
        models = _catalog_to_models(payload)
        # Sync path: apply whatever probe verdicts are already cached; a fresh
        # probe needs the async path (model fetcher calls this inside
        # asyncio.run when no loop is running, so asyncio.run works there).
        cached = _read_probe_cache()
        if cached:
            models = [m for m in models if cached.get(m["snapshot"]) is True]
        return models
