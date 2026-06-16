from vtx.config import (
    AVAILABLE_BINARIES,
    CONFIG_DIR_NAME,
    Config,
    consume_config_warnings,
    get_agents_dir,
    get_config,
    get_config_dir,
    reload_config,
    reset_config,
    set_colored_tool_badge,
    set_config,
    set_git_context,
    set_model_provider_filter,
    set_notifications_enabled,
    set_permissions_mode,
    set_show_welcome_shortcuts,
    set_theme,
    set_thinking_lines,
    update_available_binaries,
)
from vtx.context._xml import escape_xml
from vtx.core.scratchpad import get_scratchpad_dir, init_scratchpad, is_scratchpad_path


class _ConfigProxy(Config):
    """Proxy that delegates to get_config() for runtime reloading and test injection."""

    def __init__(self) -> None:
        # Do not call super().__init__(): all attribute access is delegated to
        # the live config returned by get_config() via __getattr__ below.
        pass

    def __getattr__(self, name: str):
        return getattr(get_config(), name)


config = _ConfigProxy()

__all__ = [
    "AVAILABLE_BINARIES",
    "CONFIG_DIR_NAME",
    "Config",
    "config",
    "consume_config_warnings",
    "escape_xml",
    "get_agents_dir",
    "get_config",
    "get_config_dir",
    "get_scratchpad_dir",
    "init_scratchpad",
    "is_scratchpad_path",
    "reload_config",
    "reset_config",
    "set_colored_tool_badge",
    "set_config",
    "set_git_context",
    "set_model_provider_filter",
    "set_notifications_enabled",
    "set_permissions_mode",
    "set_show_welcome_shortcuts",
    "set_theme",
    "set_thinking_lines",
    "update_available_binaries",
]
