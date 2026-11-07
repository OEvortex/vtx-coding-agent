"""Coding-agent package surface.

Re-exports resolve lazily through :func:`__getattr__` so ``import
vtx.coding_agent.cli`` does not pay for ``headless``/provider graphs it may
never touch. ``register_skills_package`` stays eager — it is a side effect
other modules rely on at import time.
"""

from __future__ import annotations

from typing import Any

from vtx.agent.context.skills import register_skills_package

# name -> owning module; the attribute name equals the export name.
_CONFIG_EXPORTS = {
    "AVAILABLE_BINARIES",
    "CONFIG_DIR_NAME",
    "CURRENT_CONFIG_VERSION",
    "Config",
    "PermissionMode",
    "add_recent_model",
    "config",
    "consume_config_warnings",
    "get_agents_dir",
    "get_config",
    "get_config_dir",
    "get_last_selected",
    "get_recent_models",
    "reload_config",
    "reset_config",
    "set_colored_tool_badge",
    "set_config",
    "set_git_context",
    "set_last_selected",
    "set_model_provider_filter",
    "set_notifications_enabled",
    "set_permissions_mode",
    "set_show_welcome_shortcuts",
    "set_theme",
    "set_thinking_lines",
    "update_available_binaries",
}
_VERSION_EXPORTS = {"PACKAGE_NAME", "VERSION", "format_version"}
_HEADLESS_EXPORTS = {"_exit_code", "render_run", "resolve_prompt", "run_headless"}

# Register coding agent skills package into harness
register_skills_package("vtx.coding_agent")


def __getattr__(name: str) -> Any:
    if name in _CONFIG_EXPORTS:
        from vtx.core import config as module

        return getattr(module, name)
    if name in _VERSION_EXPORTS:
        from vtx.core import version as module

        return getattr(module, name)
    if name in _HEADLESS_EXPORTS:
        from vtx.coding_agent import headless as module

        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AVAILABLE_BINARIES",
    "CONFIG_DIR_NAME",
    "CURRENT_CONFIG_VERSION",
    "PACKAGE_NAME",
    "VERSION",
    "Config",
    "PermissionMode",
    "_exit_code",
    "add_recent_model",
    "config",
    "consume_config_warnings",
    "format_version",
    "get_agents_dir",
    "get_config",
    "get_config_dir",
    "get_last_selected",
    "get_recent_models",
    "reload_config",
    "render_run",
    "reset_config",
    "resolve_prompt",
    "run_headless",
    "set_colored_tool_badge",
    "set_config",
    "set_git_context",
    "set_last_selected",
    "set_model_provider_filter",
    "set_notifications_enabled",
    "set_permissions_mode",
    "set_show_welcome_shortcuts",
    "set_theme",
    "set_thinking_lines",
    "update_available_binaries",
]
