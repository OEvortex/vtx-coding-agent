"""Configuration module for vtx_claw."""

from vtx_claw.config.loader import get_config_path, load_config
from vtx_claw.config.paths import (
    get_cli_history_path,
    get_cron_dir,
    get_data_dir,
    get_legacy_sessions_dir,
    get_logs_dir,
    get_media_dir,
    get_runtime_subdir,
    get_workspace_path,
    is_default_workspace,
)
from vtx_claw.config.schema import Config

__all__ = [
    "Config",
    "get_cli_history_path",
    "get_config_path",
    "get_cron_dir",
    "get_data_dir",
    "get_legacy_sessions_dir",
    "get_logs_dir",
    "get_media_dir",
    "get_runtime_subdir",
    "get_workspace_path",
    "is_default_workspace",
    "load_config",
]
