"""Path helpers — VTX-native, inspired by openjarvis config.paths."""

from __future__ import annotations

from pathlib import Path

from vtx.core.paths import get_config_dir
from vtx.openjarvis.utils.helpers import ensure_dir


def get_config_path() -> Path:
    return get_config_dir() / "openjarvis.json"


def get_data_dir() -> Path:
    return ensure_dir(get_config_dir() / "openjarvis")


def get_runtime_subdir(name: str) -> Path:
    return ensure_dir(get_data_dir() / name)


def get_media_dir(channel: str | None = None) -> Path:
    base = get_runtime_subdir("media")
    return ensure_dir(base / channel) if channel else base


def get_cron_dir() -> Path:
    return get_runtime_subdir("cron")


def get_logs_dir() -> Path:
    return ensure_dir(get_runtime_subdir("logs"))


def get_workspace_path(workspace: str | None = None) -> Path:
    path = Path(workspace).expanduser() if workspace else Path.home() / "workspace"
    return ensure_dir(path)


def is_default_workspace(workspace: str | Path | None) -> bool:
    current = Path(workspace).expanduser() if workspace is not None else Path.home() / "workspace"
    default = Path.home() / "workspace"
    return current.resolve(strict=False) == default.resolve(strict=False)


def get_cli_history_path() -> Path:
    return get_config_dir() / "history" / "cli_history"


def get_legacy_sessions_dir() -> Path:
    return get_config_dir() / "sessions_legacy"
