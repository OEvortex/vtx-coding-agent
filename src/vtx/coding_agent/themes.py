"""Color themes for Vtx TUI.

Re-exports theme definitions and registry from :mod:`vtx.tui.themes`.
"""

from __future__ import annotations

from vtx.tui.themes import (
    BadgeColorConfig,
    ColorsConfig,
    SyntaxColorConfig,
    ThemeConfig,
    ToolBgConfig,
    get_theme,
    get_theme_ids,
    get_theme_options,
)

__all__ = [
    "BadgeColorConfig",
    "ColorsConfig",
    "SyntaxColorConfig",
    "ThemeConfig",
    "ToolBgConfig",
    "get_theme",
    "get_theme_ids",
    "get_theme_options",
]
