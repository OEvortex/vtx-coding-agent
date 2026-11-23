"""DeepAI Code (deepai.org) credentials.

DeepAI Code desktop app stores its API key at
``~/.deepai-code/userdata/secrets/deepai-account.bin`` (JSON with ``apiKey``).
CTX reads that file directly so an existing desktop login is reused
automatically. ``DEEPAI_API_KEY`` env var overrides the file.

The upstream API is OpenAI-compatible but requires extra headers
(lane version, machine fingerprint, check-in nonce) — those are handled
by the provider client layer, not this credential module.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ctx.core.paths import get_config_dir

# ── Paths ────────────────────────────────────────────────────────────────────

_DEEPAI_NATIVE_DIR = Path.home() / ".deepai-code" / "userdata" / "secrets"
_DEEPAI_NATIVE_PATH = _DEEPAI_NATIVE_DIR / "deepai-account.bin"

# ── Protocol constants (mirror the Go deepai package) ────────────────────────

LANE_VERSION = "7"
APP_VERSION = "0.3.42"
ORIGIN = "https://api.deepai.org"
MODEL_BASE = "/deepai_code"


# ── Credentials ──────────────────────────────────────────────────────────────


@dataclass
class DeepAICredentials:
    """Credentials for the DeepAI Code upstream API."""

    api_key: str
    username: str | None = None
    is_pro: bool = False
    machine_id: str = ""  # SHA256 hex of the machine UUID
    machine_uuid: str = ""  # per-account random UUID for independent allowances

    # Check-in state (transient — not persisted)
    check_in_nonce: str | None = field(default=None, repr=False)
    check_in_seconds: int = field(default=600, repr=False)
    check_in_noted_at: float = field(default=0.0, repr=False)
    check_in_acked_at: float = field(default=0.0, repr=False)
    check_in_refused_until: float = field(default=0.0, repr=False)
    process_start: float = field(default_factory=time.time, repr=False)


# ── Machine fingerprint helpers ──────────────────────────────────────────────


def _detect_machine_raw() -> str:
    """Detect the machine's raw identifier (Linux: /etc/machine-id)."""
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            raw = Path(path).read_text().strip().lower()
            if len(raw) == 32 and all(c in "0123456789abcdef" for c in raw) and raw != "0" * 32:
                return raw
        except (OSError, ValueError):
            continue
    return ""


def machine_fingerprint(raw: str) -> str:
    """SHA256 hex digest of the raw machine ID (what the server expects)."""
    raw = raw.strip().lower()
    if not raw:
        return ""
    return hashlib.sha256(raw.encode()).hexdigest()


def get_machine_id() -> str:
    """Return the machine fingerprint, or a random one if detection fails."""
    env_id = os.environ.get("DEEPAI_MACHINE_ID", "").strip()
    raw = env_id or _detect_machine_raw()
    if raw:
        return machine_fingerprint(raw)
    return machine_fingerprint(str(uuid.uuid4()))


# ── Auth path ────────────────────────────────────────────────────────────────


def get_deepai_auth_path() -> Path:
    """Path where CTX caches DeepAI credentials."""
    return get_config_dir() / "deepai_auth.json"


def get_deepai_native_path() -> Path:
    """Path where the DeepAI Code desktop app stores credentials."""
    return _DEEPAI_NATIVE_PATH


# ── Load / Save ──────────────────────────────────────────────────────────────


def _load_native_credentials() -> DeepAICredentials | None:
    """Read credentials from the DeepAI Code desktop app's secrets file."""
    if not _DEEPAI_NATIVE_PATH.exists():
        return None
    try:
        data = json.loads(_DEEPAI_NATIVE_PATH.read_text(encoding="utf-8"))
        api_key = str(data.get("apiKey") or "").strip()
        if not api_key:
            return None
        return DeepAICredentials(
            api_key=api_key,
            username=data.get("username"),
            is_pro=bool(data.get("isPro", False)),
            machine_id=get_machine_id(),
            machine_uuid=str(uuid.uuid4()),
        )
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def load_deepai_credentials() -> DeepAICredentials | None:
    """Load DeepAI credentials.

    Priority:
    1. ``DEEPAI_API_KEY`` environment variable
    2. CTX cached file (``~/.ctx/deepai_auth.json``)
    3. Native DeepAI Code desktop app secrets file
    """
    # 1. Env var
    env_key = os.environ.get("DEEPAI_API_KEY", "").strip()

    # 2. CTX cache
    path = get_deepai_auth_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cached_key = str(data.get("api_key") or "").strip()
            if cached_key:
                creds = DeepAICredentials(
                    api_key=cached_key,
                    username=data.get("username"),
                    is_pro=bool(data.get("is_pro", False)),
                    machine_id=data.get("machine_id") or get_machine_id(),
                    machine_uuid=data.get("machine_uuid") or str(uuid.uuid4()),
                )
                if env_key:
                    creds.api_key = env_key
                return creds
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    # 3. Native desktop app
    if env_key:
        return DeepAICredentials(
            api_key=env_key, machine_id=get_machine_id(), machine_uuid=str(uuid.uuid4())
        )

    native = _load_native_credentials()
    if native and env_key:
        native.api_key = env_key
    return native


def save_deepai_credentials(creds: DeepAICredentials) -> None:
    """Persist DeepAI credentials to the CTX cache directory."""
    path = get_deepai_auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "api_key": creds.api_key,
        "username": creds.username,
        "is_pro": creds.is_pro,
        "machine_id": creds.machine_id,
        "machine_uuid": creds.machine_uuid,
    }
    # Strip None values
    data = {k: v for k, v in data.items() if v is not None and v != ""}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def clear_deepai_credentials() -> None:
    """Remove the CTX-cached DeepAI credentials (leaves native file intact)."""
    path = get_deepai_auth_path()
    if path.exists():
        with contextlib.suppress(OSError):
            path.unlink()


# ── Convenience checks ───────────────────────────────────────────────────────


def is_deepai_logged_in() -> bool:
    """Return True if we have a usable DeepAI API key."""
    return load_deepai_credentials() is not None


def get_valid_deepai_token_sync() -> str | None:
    """Return the current API key (no async refresh needed — key is static)."""
    creds = load_deepai_credentials()
    if creds and creds.api_key:
        return creds.api_key
    return None


# ── Check-in helpers (used by the provider client layer) ─────────────────────

CHECK_IN_MIN_INTERVAL = 30  # seconds
CHECK_IN_REFUSED_LOCK = 6 * 3600  # 6 hours


def needs_check_in(creds: DeepAICredentials) -> bool:
    """Return True if the account needs to re-check-in before inference."""
    if not creds.check_in_nonce:
        return False
    half_interval = creds.check_in_seconds / 2
    elapsed = time.time() - creds.check_in_noted_at
    return elapsed >= half_interval


def is_check_in_locked(creds: DeepAICredentials) -> bool:
    """Return True if the account is locked from check-in after a refusal."""
    return time.time() < creds.check_in_refused_until


def acknowledge_check_in(creds: DeepAICredentials, nonce: str, seconds: int) -> None:
    """Record a successful check-in response."""
    creds.check_in_nonce = nonce
    creds.check_in_seconds = max(seconds, CHECK_IN_MIN_INTERVAL)
    creds.check_in_noted_at = time.time()
    creds.check_in_acked_at = time.time()


def lock_check_in(creds: DeepAICredentials) -> None:
    """Lock check-in after a refusal (6-hour cooldown)."""
    creds.check_in_refused_until = time.time() + CHECK_IN_REFUSED_LOCK


# ── Headers builder ──────────────────────────────────────────────────────────


def build_headers(
    creds: DeepAICredentials, lane_version: str = LANE_VERSION, instance_id: str | None = None
) -> dict[str, str]:
    """Build the full set of headers for a DeepAI Code upstream request.

    This is the equivalent of the Go package's ``_commonHeaders`` + beacon.
    """
    if instance_id is None:
        instance_id = os.urandom(16).hex()

    machine_fp = (
        creds.machine_uuid and machine_fingerprint(creds.machine_uuid)
    ) or creds.machine_id

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Api-Key": creds.api_key,
        "X-DeepAI-Code-Theos": lane_version,
        "X-DeepAI-Code-Machine": machine_fp,
        "X-DeepAI-Code-Instance": instance_id,
        "User-Agent": "node",
        "Accept": "*/*",
        "Accept-Language": "*",
        "Sec-Fetch-Mode": "cors",
    }

    # Check-in nonce (sent on subsequent /theos calls)
    if creds.check_in_nonce:
        headers["X-DeepAI-Code-Check-In"] = creds.check_in_nonce

    # Beacon heartbeat
    uptime = int(time.time() - creds.process_start)
    headers["X-DeepAI-Code-Beacon"] = f"v={APP_VERSION};up={uptime};pi=0"

    return headers


# ── Model listing (called by the legacy model fetcher) ───────────────────────


def list_deepai_models_sync() -> list[dict[str, Any]]:
    """Fetch the model list from DeepAI Code using the proper lane headers.

    Returns a list of dicts with ``id``, ``name``, ``context_length``,
    ``max_tokens``, ``supports_images``, ``supports_thinking``, and ``is_free``.
    The API returns ``contextWindow``, ``maxTokens``, ``reasoning``, ``input``
    (list), ``premier`` (bool), and ``theosPerRequest`` (string cost).
    A model is free when ``premier`` is ``False`` (and ``theosPerRequest == "0"``).
    """
    creds = load_deepai_credentials()
    if creds is None:
        return []

    headers = build_headers(creds)
    headers["Accept-Encoding"] = "identity"
    url = f"{ORIGIN}{MODEL_BASE}/models"

    try:
        from urllib.request import Request, urlopen

        request = Request(url, headers=headers)
        with urlopen(request, timeout=10.0) as resp:
            if resp.status >= 400:
                return []
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []

    # DeepAI returns {"models": [...], "default": "..."}
    raw_models: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        val = payload.get("models")
        if isinstance(val, list):
            raw_models = [m for m in val if isinstance(m, dict)]
    elif isinstance(payload, list):
        raw_models = [m for m in payload if isinstance(m, dict)]

    models: list[dict[str, Any]] = []
    for m in raw_models:
        model_id = str(m.get("id") or "")
        if not model_id:
            continue

        input_mods = m.get("input") or []
        if not isinstance(input_mods, list):
            input_mods = []

        premier = bool(m.get("premier", False))
        theos = str(m.get("theosPerRequest", "1"))
        is_free = not premier and theos == "0"

        models.append(
            {
                "id": model_id,
                "name": str(m.get("name") or model_id),
                "max_tokens": int(m.get("maxTokens") or 0),
                "context_length": int(m.get("contextWindow") or 0),
                "supports_images": "image" in input_mods,
                "supports_thinking": bool(m.get("reasoning", False)),
                "is_free": is_free,
            }
        )
    return models


def register_deepai_model_limits() -> None:
    """Register DeepAI model context limits with the ContextLengthManager.

    Fetches models from the DeepAI API and registers each model's context
    window and output limits so they override the generic models.dev catalog.
    """
    raw = list_deepai_models_sync()
    if not raw:
        return

    from ctx.ai.context_length import TokenLimits, context_length_manager

    for m in raw:
        model_id = m.get("id")
        if not model_id:
            continue
        context = int(m.get("context_length") or 0)
        output = int(m.get("max_tokens") or 0)
        supports_images = bool(m.get("supports_images", False))
        supports_thinking = bool(m.get("supports_thinking", False))

        limits = TokenLimits(
            context=context,
            output=output,
            supports_reasoning=supports_thinking,
            supports_vision=supports_images,
            supports_tools=False,
            supports_audio=False,
        )
        # Register under provider scope so get_limits("model_id", provider="deepai") finds it
        context_length_manager._provider_limits.setdefault("deepai", {})[model_id] = limits
        context_length_manager._provider_limits["deepai"][model_id.lower()] = limits
        # Also register globally
        context_length_manager._limits[model_id] = limits
        context_length_manager._limits[model_id.lower()] = limits
