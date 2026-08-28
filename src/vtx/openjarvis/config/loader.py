"""Config loader — VTX-native, inspired by openjarvis config.loader."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from vtx.core.paths import get_config_dir
from vtx.openjarvis.agent.config import OpenJarvisConfig
from vtx.openjarvis.config.schema import Config


def get_config_path() -> Path:
    return get_config_dir() / "openjarvis.json"


def load_config() -> Config:
    oj = OpenJarvisConfig.load()
    return Config.from_openjarvis(oj)


def save_config(config: Config) -> None:
    """Persist ``config`` back to the on-disk openjarvis.json file."""
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.model_dump(), indent=2), encoding="utf-8")


_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _resolve_env_value(value: object) -> object:
    if isinstance(value, str):

        def _replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            env = os.environ.get(name)
            if env is not None:
                return env
            if default is not None:
                return default
            return match.group(0)  # leave unresolved references untouched

        return _ENV_VAR_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _resolve_env_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_value(v) for v in value]
    return value


def resolve_config_env_vars(config: Config) -> Config:
    """Return a copy of ``config`` with ``${VAR}`` / ``${VAR:-default}`` references resolved."""
    data = _resolve_env_value(config.model_dump())
    return Config.model_validate(data)
