"""
API-key storage for dynamic OpenAI-compatible providers.

The dynamic providers (``airouter``, ``opencode``, ``kilo``, ``tokenrouter``) do
not need an OAuth flow — they just need an API key. Users can set one of three
ways, in priority order:

1. The provider's ``<NAME>_API_KEY`` environment variable (e.g. ``KILO_API_KEY``).
2. The encrypted-on-disk key file at the configured location (mode 0600),
   written by the in-app ``/login`` command.
3. None — for providers that support a free tier (airouter, kilo), vtx will
   fall back to a placeholder key.

This module owns path #2: it reads/writes the key file and exposes a small
helper, :func:`get_dynamic_api_key`, that already implements the env-var-first
priority so the rest of vtx does not have to.

The storage location and format can be configured via the vtx-api-key-storage skill.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from vtx import get_config_dir
from vtx.llm.dynamic_models import DYNAMIC_PROVIDERS
from vtx.llm.provider_catalog import get as get_provider_info

# Default configuration
AUTH_FILENAME = "dynamic_auth.json"
Vtx_STORAGE_DIR = Path.home() / "vtx"


@dataclass
class DynamicProviderStatus:
    """Status of a dynamic provider's credentials."""

    provider: str
    env_var: str | None
    has_env_key: bool
    has_stored_key: bool
    api_key_optional: bool

    @property
    def is_configured(self) -> bool:
        """True if we have any way to authenticate (key or no-auth provider)."""
        return self.has_env_key or self.has_stored_key or self.api_key_optional


def get_dynamic_auth_path() -> Path:
    """Get the path to the API key storage file.

    This function checks for the new YAML format first, then falls back
    to the JSON format for backward compatibility.
    """
    # Check for new YAML format
    yaml_path = Vtx_STORAGE_DIR / "dynamic_auth.yml"
    if yaml_path.exists():
        return yaml_path

    # Check for JSON format in both old and new locations
    # First check XDG_CONFIG_HOME/vtx/
    xdg_config_dir = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_dir:
        xdg_path = Path(xdg_config_dir) / "vtx" / AUTH_FILENAME
        if xdg_path.exists():
            return xdg_path

    # Then check ~/.vtx/ for backward compatibility
    return get_config_dir() / AUTH_FILENAME


def _read_all() -> dict[str, str]:
    path = get_dynamic_auth_path()
    if not path.exists():
        return {}

    try:
        content = path.read_text(encoding="utf-8")

        # Determine format based on file extension
        if path.suffix.lower() == ".yml" or path.suffix.lower() == ".yaml":
            import yaml

            data = yaml.safe_load(content) or {}
        elif path.suffix.lower() == ".json":
            data = json.loads(content)
        else:
            # Default to JSON for backward compatibility
            data = json.loads(content)

    except (OSError, json.JSONDecodeError, ImportError):
        return {}
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}
    # Only keep str→str entries; ignore anything weird.
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _write_all(keys: dict[str, str]) -> None:
    path = get_dynamic_auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Determine format based on file extension
    if path.suffix.lower() == ".yml" or path.suffix.lower() == ".yaml":
        tmp = path.with_suffix(".yml.tmp")
        try:
            import yaml

            tmp.write_text(yaml.dump(keys, default_flow_style=False), encoding="utf-8")
        except ImportError:
            # Fallback to JSON if yaml not available
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(keys, indent=2), encoding="utf-8")
    else:
        # Default to JSON
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(keys, indent=2), encoding="utf-8")

    with contextlib.suppress(OSError):
        # Non-POSIX filesystems (e.g. Windows) don't support chmod; ignore.
        os.chmod(tmp, 0o600)
    tmp.replace(path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def load_api_key(provider: str) -> str | None:
    """Return the API key stored on disk for a provider, if any."""
    return _read_all().get(provider)


def save_api_key(provider: str, key: str) -> None:
    """Persist an API key for a provider."""
    key = key.strip()
    if not key:
        raise ValueError("API key must not be empty")
    if provider not in DYNAMIC_PROVIDERS and get_provider_info(provider) is None:
        raise ValueError(f"Unknown provider: {provider}")
    keys = _read_all()
    keys[provider] = key
    _write_all(keys)


def clear_api_key(provider: str) -> bool:
    """Remove a stored API key. Returns True if one was removed."""
    keys = _read_all()
    if provider not in keys:
        return False
    del keys[provider]
    _write_all(keys)
    return True


def has_api_key(provider: str) -> bool:
    """True if a stored key exists for the provider."""
    return provider in _read_all()


def _env_var_for(provider: str) -> str | None:
    config = DYNAMIC_PROVIDERS.get(provider)
    if config is not None:
        return config.env_var
    p = get_provider_info(provider)
    return p.api_key_env if p else None


def get_dynamic_api_key(provider: str) -> str | None:
    """Return the best available API key for a dynamic provider.

    Priority:
    1. ``<NAME>_API_KEY`` env var
    2. Stored ``dynamic_auth.json`` entry
    3. Provider-specific OAuth token file (e.g. ``~/.better-auth/token.json``)
    """
    env_var = _env_var_for(provider)
    if env_var:
        env_value = os.environ.get(env_var)
        if env_value and env_value.strip():
            return env_value.strip()

    stored = load_api_key(provider)
    if stored:
        return stored

    # Fall back to OAuth token files for specific providers
    if provider == "supercode":
        try:
            from .supercode import load_supercode_credentials

            creds = load_supercode_credentials()
            if creds is not None and creds.token:
                return creds.token
        except Exception:
            pass

    return None


def get_provider_status(provider: str) -> DynamicProviderStatus | None:
    """Return credential status for a provider, or ``None`` if unknown.

    Works for both the built-in ``DYNAMIC_PROVIDERS`` (airouter, opencode,
    kilo, tokenrouter) and any provider defined in ``provider.yaml``.
    """
    config = DYNAMIC_PROVIDERS.get(provider)
    if config is not None:
        env_var = config.env_var
        has_env = bool(env_var and os.environ.get(env_var, "").strip())
        return DynamicProviderStatus(
            provider=provider,
            env_var=env_var,
            has_env_key=has_env,
            has_stored_key=has_api_key(provider),
            api_key_optional=config.api_key_optional,
        )

    p = get_provider_info(provider)
    if p is None or not p.base_url:
        return None
    env_var = p.api_key_env
    has_env = bool(env_var and os.environ.get(env_var, "").strip())
    has_stored = has_api_key(provider)

    # Check OAuth token files for providers that use them
    if not has_stored and provider == "supercode":
        try:
            from .supercode import is_supercode_logged_in

            has_stored = is_supercode_logged_in()
        except Exception:
            pass

    return DynamicProviderStatus(
        provider=provider,
        env_var=env_var,
        has_env_key=has_env,
        has_stored_key=has_stored,
        api_key_optional=p.api_key_optional,
    )


__all__ = [
    "AUTH_FILENAME",
    "DynamicProviderStatus",
    "clear_api_key",
    "get_dynamic_api_key",
    "get_dynamic_auth_path",
    "get_provider_status",
    "has_api_key",
    "load_api_key",
    "save_api_key",
]
