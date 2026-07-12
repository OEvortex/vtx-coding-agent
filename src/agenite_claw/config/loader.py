"""Configuration loading utilities."""

import json
import os
import re
from pathlib import Path
from typing import Any

import pydantic
from loguru import logger
from pydantic import BaseModel

from agenite_claw.config.schema import Config, _resolve_tool_config_refs

# Global variable to store current config path (for multi-instance support)
_current_config_path: Path | None = None
_schema_refs_ready = False


def set_config_path(path: Path) -> None:
    """Set the current config path (used to derive data directory)."""
    global _current_config_path
    _current_config_path = path


def get_config_path() -> Path:
    """Get the configuration file path."""
    if _current_config_path:
        return _current_config_path
    new_path = Path.home() / ".vtx" / "claw" / "config.json"
    old_path = Path.home() / ".agenite_claw" / "config.json"
    # Auto-migrate from legacy location on first access
    if not new_path.exists() and old_path.exists():
        new_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copy2(str(old_path), str(new_path))
    return new_path


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    global _schema_refs_ready
    if not _schema_refs_ready:
        _resolve_tool_config_refs()
        _schema_refs_ready = True

    path = config_path or get_config_path()

    config = Config()
    data: dict[str, Any] | None = None
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            # Note: providers section may contain non-API-key settings (api_base, api_type, etc.)
            # that are persisted in config.json. API keys for known vtx providers are stored in
            # vtx's dynamic_auth.json via save_config(). The vtx bridge (merge_vtx_config) merges
            # API keys from vtx into the config at runtime.
            data = _migrate_config(data)
            config = Config.model_validate(data)
        except (json.JSONDecodeError, ValueError, pydantic.ValidationError) as e:
            raise ValueError(f"Failed to load config from {path}: {e}") from e

    _apply_ssrf_whitelist(config)

    if config_path is None:
        try:
            from agenite_claw._vtx_bridge import merge_vtx_config

            config = merge_vtx_config(config)
        except Exception as e:
            logger.error("Failed to merge VTX configuration: {}", e)

    return config


def _apply_ssrf_whitelist(config: Config) -> None:
    """Apply SSRF whitelist from config to the network security module."""
    from agenite_claw.security.network import configure_ssrf_whitelist

    configure_ssrf_whitelist(config.tools.ssrf_whitelist)


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(mode="json", by_alias=True)

    # Handle providers: save API keys to vtx's dynamic_auth.json for known vtx providers,
    # keep other settings (apiBase, apiType, etc.) and custom provider API keys in config.json.
    # This allows provider configuration from the WebUI.
    if "providers" in data:
        providers_data = data["providers"]

        # Extract API keys and save them to vtx's dynamic_auth.json
        # Also handle clearing of API keys (empty string or None)
        # Provider configs can be in model fields (e.g., custom) or model_extra
        api_keys_to_save = {}
        api_keys_to_clear = []

        # Get the list of known vtx providers
        try:
            from vtx.llm.dynamic_models import DYNAMIC_PROVIDERS

            known_providers = set(DYNAMIC_PROVIDERS.keys())
            for p in DYNAMIC_PROVIDERS.values():
                if hasattr(p, "name"):
                    known_providers.add(p.name)
            # Also check provider catalog
            try:
                from vtx.llm.provider_catalog import list_providers as list_vtx_providers

                for p in list_vtx_providers():
                    known_providers.add(p.slug)
            except Exception:
                pass
        except Exception as e:
            logger.debug("Could not determine known vtx providers: {}", e)
            known_providers = set()

        # Process all provider entries
        if isinstance(providers_data, dict):
            for provider_name, provider_config in providers_data.items():
                if isinstance(provider_config, dict):
                    api_key = provider_config.get("apiKey")
                    # Non-None and non-empty string
                    if api_key and provider_name in known_providers:
                        api_keys_to_save[provider_name] = api_key
                    # Empty string - clear the key
                    elif api_key is not None and provider_name in known_providers:
                        api_keys_to_clear.append(provider_name)

        # Save API keys to vtx's dynamic_auth.json
        if api_keys_to_save:
            try:
                from vtx.llm.oauth.dynamic import save_api_key

                for provider_name, api_key in api_keys_to_save.items():
                    try:
                        save_api_key(provider_name, api_key)
                    except ValueError as e:
                        # Provider might not be in vtx's list, skip it
                        logger.debug("Skipping save_api_key for {}: {}", provider_name, e)
            except Exception as e:
                logger.error("Failed to save API keys to vtx dynamic_auth.json: {}", e)

        # Clear API keys that were set to empty string
        if api_keys_to_clear:
            try:
                from vtx.llm.oauth.dynamic import clear_api_key

                for provider_name in api_keys_to_clear:
                    try:
                        clear_api_key(provider_name)
                    except Exception as e:
                        logger.debug("Skipping clear_api_key for {}: {}", provider_name, e)
            except Exception as e:
                logger.error("Failed to clear API keys from vtx dynamic_auth.json: {}", e)

        # Remove apiKey from all provider configs before saving to config.json
        # Keep other settings like apiBase, apiType, etc.
        # For known vtx providers, apiKey is saved to dynamic_auth.json and removed from config
        # For custom providers (not in vtx), apiKey is kept in config.json
        if isinstance(providers_data, dict):
            for provider_name, provider_config in providers_data.items():
                if isinstance(provider_config, dict) and provider_name in known_providers:
                    provider_config.pop("apiKey", None)

        # Only remove providers section if it's empty after removing API keys
        # If there are other settings (apiBase, apiType, etc.), keep them in config.json
        if not any(
            v
            for v in providers_data.values()
            if isinstance(v, dict) and v  # Non-empty dict
        ):
            del data["providers"]

    if config_path is None:
        try:
            from vtx.config import get_last_selected, set_last_selected

            last_sel = get_last_selected()
            effective_preset = config.resolve_preset()
            set_last_selected(
                model_id=effective_preset.model,
                provider=effective_preset.provider,
                thinking_level=last_sel.thinking_level,
                agent=last_sel.agent,
            )
        except Exception as e:
            logger.error("Failed to sync selection to VTX: {}", e)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


_ENV_REF_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_config_env_vars(config: Config) -> Config:
    """Return *config* with ``${VAR}`` env-var references resolved.

    Walks in place so fields declared with ``exclude=True`` survive;
    returns the same instance when no references are present.
    Raises ``ValueError`` if a referenced variable is not set.
    """
    return _resolve_in_place(config)


def _resolve_in_place(obj: Any) -> Any:
    if isinstance(obj, str):
        new = _ENV_REF_PATTERN.sub(_env_replace, obj)
        return new if new != obj else obj
    if isinstance(obj, BaseModel):
        updates: dict[str, Any] = {}
        for name in type(obj).model_fields:
            old = getattr(obj, name)
            new = _resolve_in_place(old)
            if new is not old:
                updates[name] = new
        extras = obj.__pydantic_extra__
        new_extras: dict[str, Any] | None = None
        if extras:
            resolved = {k: _resolve_in_place(v) for k, v in extras.items()}
            if any(resolved[k] is not extras[k] for k in extras):
                new_extras = resolved
        if not updates and new_extras is None:
            return obj
        copy = obj.model_copy(update=updates) if updates else obj.model_copy()
        if new_extras is not None:
            copy.__pydantic_extra__ = new_extras
        return copy
    if isinstance(obj, dict):
        resolved = {k: _resolve_in_place(v) for k, v in obj.items()}
        return resolved if any(resolved[k] is not obj[k] for k in obj) else obj
    if isinstance(obj, list):
        resolved = [_resolve_in_place(v) for v in obj]
        return (
            resolved if any(nv is not ov for nv, ov in zip(resolved, obj, strict=False)) else obj
        )
    return obj


def _resolve_env_vars(obj: object) -> object:
    """Recursively resolve ``${VAR}`` patterns in plain strings/dicts/lists."""
    if isinstance(obj, str):
        return _ENV_REF_PATTERN.sub(_env_replace, obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(v) for v in obj]
    return obj


def _env_replace(match: re.Match[str]) -> str:
    name = match.group(1)
    value = os.environ.get(name)
    if value is None:
        raise ValueError(f"Environment variable '{name}' referenced in config is not set")
    return value


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    agents = data.get("agents", {})
    defaults = agents.get("defaults", {}) if isinstance(agents, dict) else {}
    if isinstance(defaults, dict):
        had_legacy_max_messages = "maxMessages" in defaults or "max_messages" in defaults
        defaults.pop("maxMessages", None)
        defaults.pop("max_messages", None)
        if had_legacy_max_messages:
            # TODO(next version): Remove this legacy cleanup branch; the schema
            # will silently ignore this field once the warning grace period ends.
            logger.warning(
                "agents.defaults.maxMessages/max_messages is legacy and ignored; "
                "replay max messages is now an internal safety cap. Remove it from "
                "config. This compatibility warning will be removed in the next version."
            )

    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")

    # Move tools.myEnabled / tools.mySet → tools.my.{enable, allowSet}.
    # The old flat keys shipped in the initial MyTool landing; wrapping them in a
    # sub-config keeps `web` / `exec` / `my` symmetric and gives room to grow.
    if "myEnabled" in tools or "mySet" in tools:
        my_cfg = tools.setdefault("my", {})
        if "myEnabled" in tools and "enable" not in my_cfg:
            my_cfg["enable"] = tools.pop("myEnabled")
        else:
            tools.pop("myEnabled", None)
        if "mySet" in tools and "allowSet" not in my_cfg:
            my_cfg["allowSet"] = tools.pop("mySet")
        else:
            tools.pop("mySet", None)

    return data
