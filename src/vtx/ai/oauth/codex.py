"""
OpenAI Codex OAuth flow.

Mirrors the Codex CLI auth so VTX can use a ChatGPT/Codex subscription
without an API key.  Supports both the browser authorization-code flow
and the headless device-code flow, and falls back to ``~/.codex/auth.json``
so an existing Codex CLI login is reused automatically.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

from vtx.core.paths import get_config_dir

_CLIENT_ID = os.getenv("CODEX_APP_SERVER_LOGIN_CLIENT_ID", "app_EMoamEEZ73f0CkXaXp7hrann")
_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
_TOKEN_URL = "https://auth.openai.com/oauth/token"
_REFRESH_TOKEN_URL_OVERRIDE = os.getenv("CODEX_REFRESH_TOKEN_URL_OVERRIDE")
_DEVICE_CODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
_DEVICE_POLL_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
_REDIRECT_URI = "http://localhost:1455/auth/callback"
_SCOPE = "openid profile email offline_access api.connectors.read api.connectors.invoke"
_JWT_CLAIM_PATH = "https://api.openai.com/auth"
_SUCCESS_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8" /><title>Authentication successful</title></head>
<body><p>Authentication successful. Return to your terminal to continue.</p></body>
</html>"""

# Codex CLI default originator.
_DEFAULT_ORIGINATOR = "codex_cli_rs"


@dataclass
class CodexCredentials:
    refresh: str
    access: str
    expires: int
    account_id: str
    id_token: str | None = None


def get_codex_native_path() -> Path:
    return Path.home() / ".codex" / "auth.json"


def get_codex_auth_path() -> Path:
    preferred = get_config_dir() / "codex_auth.json"
    if preferred.exists():
        return preferred
    legacy = get_config_dir() / "openai_auth.json"
    if legacy.exists():
        return legacy
    return preferred


def _decode_jwt_payload(token: str) -> dict[str, Any] | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        if payload is None:
            return None
        padded = payload + "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode()).decode()
        return json.loads(decoded)
    except Exception:
        return None


def _extract_account_id(access_token: str) -> str | None:
    payload = _decode_jwt_payload(access_token)
    if not payload:
        return None
    auth = payload.get(_JWT_CLAIM_PATH)
    if not isinstance(auth, dict):
        return None
    account_id = auth.get("chatgpt_account_id")
    return account_id if isinstance(account_id, str) and account_id else None


def _load_native_credentials() -> CodexCredentials | None:
    path = get_codex_native_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        tokens = data.get("tokens")
        if not isinstance(tokens, dict):
            return None
        access = tokens.get("access_token")
        refresh = tokens.get("refresh_token")
        id_token = tokens.get("id_token")
        if not isinstance(access, str) or not isinstance(refresh, str):
            return None
        account_id = tokens.get("account_id")
        if not account_id:
            account_id = _extract_account_id(access)
        expires = int(time.time() * 1000) + 3600 * 1000
        if isinstance(tokens.get("expires_at"), (int, float)):
            expires = int(tokens["expires_at"])
        return CodexCredentials(
            refresh=refresh,
            access=access,
            expires=expires,
            account_id=account_id or "",
            id_token=id_token if isinstance(id_token, str) else None,
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def load_codex_credentials() -> CodexCredentials | None:
    path = get_codex_auth_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return CodexCredentials(
                refresh=data["refresh"],
                access=data["access"],
                expires=int(data["expires"]),
                account_id=data.get("account_id", ""),
                id_token=data.get("id_token"),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return _load_native_credentials()


def save_codex_credentials(creds: CodexCredentials) -> None:
    path = get_config_dir() / "codex_auth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "refresh": creds.refresh,
                "access": creds.access,
                "expires": creds.expires,
                "account_id": creds.account_id,
                "id_token": creds.id_token,
            },
            indent=2,
        )
    )
    path.chmod(0o600)


def clear_codex_credentials() -> None:
    for filename in ("codex_auth.json", "openai_auth.json"):
        path = get_config_dir() / filename
        if path.exists():
            path.unlink()


def is_codex_logged_in() -> bool:
    return load_codex_credentials() is not None


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _generate_pkce() -> tuple[str, str]:
    verifier = _base64url_encode(secrets.token_bytes(32))
    challenge = _base64url_encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def _create_state() -> str:
    return secrets.token_hex(16)


def _build_authorize_url(code_challenge: str, state: str, originator: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "scope": _SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": originator,
        }
    )
    return f"{_AUTHORIZE_URL}?{query}"


async def _exchange_code_for_tokens(code: str, verifier: str) -> CodexCredentials:
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            _TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "client_id": _CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": _REDIRECT_URI,
            },
        ) as response,
    ):
        if response.status >= 400:
            text = await response.text()
            raise RuntimeError(
                f"OpenAI Codex OAuth token exchange failed ({response.status}): {text}"
            )
        data = await response.json()

    access = data.get("access_token")
    refresh = data.get("refresh_token")
    expires_in = data.get("expires_in")
    id_token = data.get("id_token")
    if (
        not isinstance(access, str)
        or not isinstance(refresh, str)
        or not isinstance(expires_in, int)
    ):
        raise RuntimeError("OpenAI Codex OAuth token response missing required fields")

    account_id = _extract_account_id(access)
    if not account_id:
        account_id = data.get("account_id") or ""

    return CodexCredentials(
        access=access,
        refresh=refresh,
        expires=int(time.time() * 1000) + expires_in * 1000,
        account_id=account_id,
        id_token=id_token if isinstance(id_token, str) else None,
    )


async def _start_callback_server(state: str) -> tuple[asyncio.AbstractServer, asyncio.Future[str]]:
    loop = asyncio.get_running_loop()
    code_future: asyncio.Future[str] = loop.create_future()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await reader.read(4096)
            request_line = raw.decode(errors="ignore").splitlines()[0] if raw else ""
            parts = request_line.split()
            if len(parts) < 2:
                return

            path = parts[1]
            parsed = urlparse(path)
            query = parse_qs(parsed.query)

            if parsed.path != "/auth/callback":
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot found")
                await writer.drain()
                return

            req_state = (query.get("state") or [None])[0]
            code = (query.get("code") or [None])[0]

            if req_state != state or not isinstance(code, str) or not code:
                writer.write(
                    b"HTTP/1.1 400 Bad Request\r\nContent-Length: 14\r\n\r\nState mismatch"
                )
                await writer.drain()
                return

            body = _SUCCESS_HTML.encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()

            if not code_future.done():
                code_future.set_result(code)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    server = await asyncio.start_server(handler, "localhost", 1455)
    return server, code_future


def _parse_manual_input(input_text: str) -> tuple[str | None, str | None]:
    text = input_text.strip()
    if not text:
        return None, None

    try:
        parsed = urlparse(text)
        if parsed.scheme and parsed.netloc:
            query = parse_qs(parsed.query)
            return (query.get("code") or [None])[0], (query.get("state") or [None])[0]
    except Exception:
        pass

    if "code=" in text:
        query = parse_qs(text)
        return (query.get("code") or [None])[0], (query.get("state") or [None])[0]

    if "#" in text:
        code, st = text.split("#", 1)
        return code or None, st or None

    return text, None


async def login(
    on_auth_url: Any | None = None,
    on_manual_input: Any | None = None,
    originator: str | None = None,
) -> CodexCredentials:
    originator = originator or os.getenv("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", _DEFAULT_ORIGINATOR)
    verifier, challenge = _generate_pkce()
    state = _create_state()
    auth_url = _build_authorize_url(challenge, state, originator)

    if on_auth_url:
        on_auth_url(auth_url)

    code: str | None = None
    server: asyncio.AbstractServer | None = None
    callback_awaitable: asyncio.Future[str] | None = None
    manual_task: asyncio.Task[Any] | None = None

    try:
        try:
            server, callback_awaitable = await _start_callback_server(state)
        except OSError:
            callback_awaitable = None

        if on_manual_input:
            manual_task = asyncio.create_task(on_manual_input())

        if not callback_awaitable and not manual_task:
            raise RuntimeError(
                "OpenAI Codex OAuth failed: could not start callback server on port 1455 "
                "and no manual input handler provided."
            )

        if callback_awaitable and manual_task:
            done, pending = await asyncio.wait(
                {callback_awaitable, manual_task}, return_when=asyncio.FIRST_COMPLETED, timeout=300
            )
            for task in pending:
                task.cancel()

            if callback_awaitable in done:
                code = callback_awaitable.result()
            elif manual_task in done:
                manual_input = manual_task.result()
                parsed_code, parsed_state = _parse_manual_input(str(manual_input))
                if parsed_state and parsed_state != state:
                    raise RuntimeError("OpenAI Codex OAuth state mismatch")
                code = parsed_code

        elif callback_awaitable:
            code = await asyncio.wait_for(callback_awaitable, timeout=300)

        elif manual_task:
            manual_input = await manual_task
            parsed_code, parsed_state = _parse_manual_input(str(manual_input))
            if parsed_state and parsed_state != state:
                raise RuntimeError("OpenAI Codex OAuth state mismatch")
            code = parsed_code

        if not code:
            raise TimeoutError(
                "OpenAI Codex OAuth timed out waiting for authorization callback on port 1455."
            )

        creds = await _exchange_code_for_tokens(code, verifier)
        save_codex_credentials(creds)
        return creds

    finally:
        if callback_awaitable and not callback_awaitable.done():
            callback_awaitable.cancel()
        if manual_task and not manual_task.done():
            manual_task.cancel()
        if server:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()


codex_login = login


async def _request_device_code() -> dict[str, Any]:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            _DEVICE_CODE_URL,
            headers={"Content-Type": "application/json"},
            json={"client_id": _CLIENT_ID},
        ) as response:
            if response.status == 404:
                raise RuntimeError(
                    "Device code login is not enabled for this Codex server. "
                    "Use the browser login or verify the server URL."
                )
            if response.status >= 400:
                text = await response.text()
                raise RuntimeError(
                    f"OpenAI Codex device code request failed ({response.status}): {text}"
                )
            return await response.json()


async def _poll_device_token(device_auth_id: str, user_code: str, interval: int) -> dict[str, Any]:
    deadline = time.time() + 900
    poll_interval = max(1, interval)
    async with aiohttp.ClientSession() as session:
        while time.time() < deadline:
            async with session.post(
                _DEVICE_POLL_URL,
                headers={"Content-Type": "application/json"},
                json={"device_auth_id": device_auth_id, "user_code": user_code},
            ) as response:
                if response.status == 403 or response.status == 404:
                    await asyncio.sleep(poll_interval)
                    continue
                if response.status >= 400:
                    text = await response.text()
                    raise RuntimeError(
                        f"OpenAI Codex device code poll failed ({response.status}): {text}"
                    )
                data = await response.json()
                if data.get("authorization_code"):
                    return data

            await asyncio.sleep(poll_interval)

    raise TimeoutError("OpenAI Codex device code flow timed out")


async def login_with_device_code(
    on_user_code: Any | None = None, originator: str | None = None
) -> CodexCredentials:
    originator = originator or os.getenv("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", _DEFAULT_ORIGINATOR)
    device = await _request_device_code()
    verification_url = f"https://auth.openai.com/codex/device"
    user_code = device.get("user_code") or device.get("usercode", "")
    device_auth_id = device.get("device_auth_id")
    interval = int(device.get("interval", 5))

    if on_user_code:
        on_user_code(verification_url, user_code)

    if not device_auth_id:
        raise RuntimeError("OpenAI Codex device code response missing device_auth_id")

    poll_result = await _poll_device_token(device_auth_id, user_code, interval)
    code = poll_result.get("authorization_code")
    if not isinstance(code, str) or not code:
        raise RuntimeError("OpenAI Codex device code flow did not return an authorization code")

    code_verifier = poll_result.get("code_verifier")
    if not isinstance(code_verifier, str):
        raise RuntimeError("OpenAI Codex device code response missing code_verifier")

    creds = await _exchange_code_for_tokens(code, code_verifier)
    save_codex_credentials(creds)
    return creds


async def refresh_codex_token(creds: CodexCredentials) -> CodexCredentials:
    refresh_url = _REFRESH_TOKEN_URL_OVERRIDE or _TOKEN_URL
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            refresh_url,
            headers={
                "Content-Type": "application/json",
                "originator": os.getenv("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", _DEFAULT_ORIGINATOR),
            },
            json={
                "client_id": _CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": creds.refresh,
            },
        ) as response,
    ):
        if response.status >= 400:
            text = await response.text()
            raise RuntimeError(
                f"OpenAI Codex OAuth token refresh failed ({response.status}): {text}"
            )
        data = await response.json()

    access = data.get("access_token")
    refresh = data.get("refresh_token")
    expires_in = data.get("expires_in")
    if (
        not isinstance(access, str)
        or not isinstance(refresh, str)
        or not isinstance(expires_in, int)
    ):
        raise RuntimeError("OpenAI Codex OAuth refresh response missing required fields")

    account_id = _extract_account_id(access)
    if not account_id:
        account_id = creds.account_id

    refreshed = CodexCredentials(
        access=access,
        refresh=refresh,
        expires=int(time.time() * 1000) + expires_in * 1000,
        account_id=account_id,
        id_token=data.get("id_token") if isinstance(data.get("id_token"), str) else creds.id_token,
    )
    save_codex_credentials(refreshed)
    return refreshed


async def get_valid_codex_credentials() -> CodexCredentials | None:
    creds = load_codex_credentials()
    if not creds:
        return None

    if time.time() * 1000 >= creds.expires - 60_000:
        try:
            creds = await refresh_codex_token(creds)
        except Exception:
            return None

    return creds


async def get_valid_codex_token() -> str | None:
    creds = await get_valid_codex_credentials()
    return creds.access if creds else None


def get_valid_codex_token_sync() -> str | None:
    """Sync fallback for model catalog / provider init (no async refresh).

    Returns a token from env or saved OpenAI Codex OAuth credentials.
    """
    env = os.getenv("OPENAI_API_KEY", "").strip()
    if env:
        return env
    creds = load_codex_credentials()
    if creds and creds.access:
        return creds.access
    return None
