"""``/settings``, ``/themes``, ``/permissions``, ``/mode``, ``/thinking``,
``/notifications`` — the settings surface, ported from ctx-code's settings
mixin, plus the vx-native /mode command inspired by fx."""

from __future__ import annotations

from typing import cast

from ctx.core.config import (
    NOTIFICATION_MODES,
    THINKING_LINES_OPTIONS,
    ThinkingLinesOption,
    config,
    set_colored_tool_badge,
    set_git_context,
    set_notifications_enabled,
    set_permissions_mode,
    set_show_welcome_shortcuts,
    set_theme,
    set_thinking_lines,
)
from ctx.core.config import PermissionMode as CtxPermissionMode

from vx import prefs
from vx.commands.base import CommandSupport
from vx.permissions import BUILTIN_MODES, mode_spec
from vx.tui import footer
from vx.tui import theme as theme_module

#: What each effort level buys, so the picker is a menu and not a wall of names.
THINKING_LEVEL_DESCRIPTIONS = {
    "none": "no reasoning, fastest",
    "minimal": "minimal reasoning",
    "low": "light reasoning",
    "medium": "balanced reasoning",
    "high": "deep reasoning",
    "xhigh": "very deep, slower",
    "max": "maximum effort, slowest",
    "default": "model decides",
}

PERMISSION_MODE_DESCRIPTIONS = {
    "prompt": "ask before mutating tool calls",
    "auto": "allow tool calls without approval prompts",
}

THINKING_LINES_DESCRIPTIONS = {
    "1": "show 1 line",
    "2": "show 2 lines",
    "3": "show 3 lines",
    "4": "show 4 lines",
    "5": "show 5 lines",
    "none": "no truncation",
}


class SettingsCommands(CommandSupport):
    # -- /permissions ----------------------------------------------------

    def _handle_permissions_command(self, args: str) -> None:
        requested = args.strip().lower()
        if requested:
            mode = {"prompt": "ask"}.get(requested, requested)
            if mode not in {spec.id for spec in BUILTIN_MODES}:
                modes = ", ".join(spec.id for spec in BUILTIN_MODES)
                self._fail(f"invalid permission mode: {requested}. use one of: {modes}")
                return
            self._select_permission_mode(mode)
            return

        perm_runtime = self._get_permission_runtime()
        if perm_runtime is None:
            self._fail("permission runtime not available", topic="permissions")
            return
        open_panel = getattr(self, "open_permissions_panel", None)
        if callable(open_panel):
            open_panel()
            return
        self._open_mode_picker(
            footer.ModePickerView(
                modes=[
                    footer.MenuItem(label=spec.name, detail=spec.description, value=spec.id)
                    for spec in BUILTIN_MODES
                ],
                selected=next(
                    (i for i, spec in enumerate(BUILTIN_MODES) if spec.id == perm_runtime.mode), 0
                ),
                current_mode=perm_runtime.mode,
            ),
            perm_runtime,
        )

    def _select_permission_mode(self, mode: str) -> None:
        perm_runtime = self._get_permission_runtime()
        if perm_runtime is None:
            self._fail("permission runtime not available", topic="permissions")
            return
        perm_runtime.set_mode(mode)
        _sync_mode_to_config(mode)

    # -- /mode -----------------------------------------------------------

    def _handle_mode_command(self, args: str) -> None:
        """Switch vx's permission mode: ask or auto."""
        perm_runtime = self._get_permission_runtime()
        if perm_runtime is None:
            self._fail("permission runtime not available", topic="mode")
            return

        requested = args.strip().lower()
        if requested:
            if mode_spec(requested) is None:
                valid = ", ".join(m.id for m in BUILTIN_MODES)
                self._fail(f"invalid mode: {requested}. use one of: {valid}")
                return
            perm_runtime.set_mode(requested)
            # Also sync the ctx config so the hint row picks it up
            _sync_mode_to_config(requested)
            return

        # Show picker
        current = perm_runtime.mode
        items = [
            footer.MenuItem(label=spec.name, detail=spec.description, value=spec.id)
            for spec in BUILTIN_MODES
        ]
        index = next((i for i, s in enumerate(BUILTIN_MODES) if s.id == current), 0)

        view = footer.ModePickerView(modes=items, selected=index, current_mode=current)
        self._open_mode_picker(view, perm_runtime)

    def _open_mode_picker(self, view: footer.ModePickerView, perm_runtime) -> None:
        """Open the mode picker overlay."""
        self._menu = {
            "kind": "mode",
            "view": view,
            "selected": view.selected,
            "perm_runtime": perm_runtime,
            "filterable": False,
            "noun": "Modes",
            "type_hint": False,
            "variants": (
                "↑↓ navigate     enter select     esc close",
                "↑↓ move  enter  esc",
                "enter select  esc close",
            ),
        }

    # -- /thinking and /effort -------------------------------------------

    def _thinking_levels(self) -> list[str]:
        rt = self.runtime
        if rt is None:
            return []
        try:
            return list(rt.effective_thinking_levels)
        except Exception:
            return []

    def _handle_thinking_command(self, args: str) -> None:
        rt = self._require_runtime()
        if rt is None:
            return
        if rt.provider is None:
            self._fail("agent not initialized", topic="thinking")
            return
        levels = self._thinking_levels()
        if not levels:
            self._fail("thinking is not available for this model", topic="thinking")
            return

        requested = args.strip()
        if requested:
            if requested not in levels:
                self._fail(f"invalid thinking level: {requested}. use one of: {', '.join(levels)}")
                return
            self._select_thinking_level(requested)
            return

        current = rt.thinking_level or ""
        items = [
            footer.MenuItem(
                label=f"{level} ✓" if level == current else level,
                detail=THINKING_LEVEL_DESCRIPTIONS.get(level, ""),
                value=level,
            )
            for level in levels
        ]
        index = next((i for i, level in enumerate(levels) if level == current), 0)
        self._open_menu(
            items,
            noun="Effort",
            on_select=lambda item: self._select_thinking_level(item.value or item.label),
            selected=index,
        )

    def _select_thinking_level(self, level: str) -> None:
        rt = self.runtime
        if rt is None or rt.provider is None:
            return
        rt.set_thinking_level(level)

    def _thinking_note(self) -> str:
        """Say what the levels apply to, as ctx-code's picker footer does."""
        rt = self.runtime
        if rt is None:
            return ""
        levels = self._thinking_levels()
        model = rt.model or "the current model"
        detail = ""
        try:
            from ctx.ai.models import get_model
            from ctx.ai.thinking import get_supported_thinking_levels

            info = get_model(rt.model, rt.model_provider)
            if info is None:
                detail = " (catalog entry not found)"
            elif not info.supports_thinking:
                detail = " (model has no reasoning support)"
            elif info.thinking_level_map:
                advertised = get_supported_thinking_levels(
                    reasoning=True, thinking_level_map=info.thinking_level_map
                )
                offered = set(levels)
                missing = [
                    "none" if level == "off" else level
                    for level in advertised
                    if ("none" if level == "off" else level) not in offered
                ]
                if missing:
                    detail = f" (not offered: {', '.join(missing)})"
        except Exception:
            detail = ""
        return f"effort for {model} — {len(levels)} levels available{detail}"

    # -- /themes ---------------------------------------------------------

    def _handle_themes_command(self, args: str) -> None:
        requested = args.strip()
        if requested:
            self._select_theme(requested)
            return
        options = theme_module.available_themes()
        current = theme_module.current().name
        items = [
            footer.MenuItem(
                label=f"{name} ✓" if name == current else name,
                detail=label,
                metadata="current" if name == current else "",
                value=name,
            )
            for name, label in options
        ]
        index = next((i for i, (name, _) in enumerate(options) if name == current), 0)
        self._open_menu(
            items,
            noun="Themes",
            on_select=lambda item: self._select_theme(item.value or item.label),
            selected=index,
            filterable=True,
        )

    def _select_theme(self, name: str) -> None:
        try:
            applied = theme_module.apply_named(name)
        except ValueError as exc:
            self._fail(str(exc))
            return
        # fx's own palettes are not ctx themes, so only ctx ids are persisted
        # — otherwise the next ctx-code run would read a theme it never wrote.
        if applied.name not in ("fx-dark", "fx-light"):
            try:
                set_theme(applied.name)
            except Exception as exc:
                self._warn(f"theme applied but not saved: {exc}")
        self._ok(f"theme changed to {applied.name}", topic="theme")
        self._draw(force=True)

    # -- /notifications --------------------------------------------------

    def _handle_notifications_command(self, args: str) -> None:
        current = "on" if config.notifications.enabled else "off"
        requested = args.strip()
        if requested:
            if requested not in NOTIFICATION_MODES:
                self._fail(f"invalid mode: {requested}. use on or off")
                return
            self._select_notifications(requested)
            return
        self._pick_choice(
            "Notifications",
            NOTIFICATION_MODES,
            current,
            self._select_notifications,
            {"on": "play notification sounds", "off": "disable notification sounds"},
        )

    def _select_notifications(self, mode: str) -> None:
        set_notifications_enabled(mode == "on")
        self._ok(f"notifications turned {mode}", topic="notifications")

    # -- /settings -------------------------------------------------------

    def _settings_rows(self) -> list[footer.MenuItem]:
        rt = self.runtime
        thinking = "-"
        try:
            thinking = (rt.thinking_level if rt else None) or "off"
        except Exception:
            thinking = "off"
        return [
            footer.MenuItem(
                label="colored-tool-badge",
                detail="on" if config.ui.colored_tool_badge else "off",
                value="colored-tool-badge",
            ),
            footer.MenuItem(
                label="git-context",
                detail="on" if config.llm.system_prompt.git_context else "off",
                value="git-context",
            ),
            footer.MenuItem(label="history", detail=prefs.history_label(), value="history"),
            footer.MenuItem(
                label="notifications",
                detail="on" if config.notifications.enabled else "off",
                value="notifications",
            ),
            footer.MenuItem(label="sidebar", detail=prefs.sidebar_label(), value="sidebar"),
            footer.MenuItem(
                label="show-shortcuts",
                detail="on" if config.ui.show_welcome_shortcuts else "off",
                value="show-shortcuts",
            ),
            footer.MenuItem(
                label="permissions", detail=config.permissions.mode, value="permissions"
            ),
            footer.MenuItem(label="themes", detail=theme_module.current().name, value="themes"),
            footer.MenuItem(label="thinking", detail=thinking, value="thinking"),
            footer.MenuItem(
                label="thinking-lines",
                detail=str(config.ui.thinking_lines),
                value="thinking-lines",
            ),
        ]

    def _handle_settings_command(self, args: str = "") -> None:
        if args.strip():
            self._warn("usage: /settings")
            return
        self._show_settings_picker()

    def _show_settings_picker(self, selected_value: str | None = None) -> None:
        items = self._settings_rows()
        index = next((i for i, item in enumerate(items) if item.value == selected_value), 0)
        self._open_menu(
            items, noun="Settings", on_select=self._handle_settings_select, selected=index
        )

    def _handle_settings_select(self, item: footer.MenuItem) -> None:
        value = item.value or item.label
        if value == "notifications":
            self._select_notifications("off" if config.notifications.enabled else "on")
        elif value == "show-shortcuts":
            enabled = not config.ui.show_welcome_shortcuts
            set_show_welcome_shortcuts(enabled)
            self._ok(f"welcome shortcuts turned {'on' if enabled else 'off'}", topic="settings")
        elif value == "permissions":
            mode = "auto" if config.permissions.mode == "prompt" else "prompt"
            self._select_permission_mode(mode)
        elif value == "colored-tool-badge":
            enabled = not config.ui.colored_tool_badge
            set_colored_tool_badge(enabled)
            self._ok(f"colored tool badge turned {'on' if enabled else 'off'}", topic="settings")
        elif value == "history":
            self._select_history_rows()
            return
        elif value == "sidebar":
            # Flip what is on screen, the way ctrl+t does; the row then says the
            # panel is pinned rather than following the work.
            shown = not self._show_sidebar
            self.set_sidebar_shown(shown)
            self._ok(f"sidebar turned {'on' if shown else 'off'}", topic="settings")
        elif value == "git-context":
            enabled = not config.llm.system_prompt.git_context
            set_git_context(enabled)
            self._ok(f"git context turned {'on' if enabled else 'off'}", topic="settings")
            self._warn("git context applies to new conversations (/new) or after a restart")
        elif value == "themes":
            self._handle_themes_command("")
            return
        elif value == "thinking":
            self._handle_thinking_command("")
            return
        elif value == "thinking-lines":
            self._pick_choice(
                "Thinking lines",
                THINKING_LINES_OPTIONS,
                config.ui.thinking_lines,
                self._select_thinking_lines,
                THINKING_LINES_DESCRIPTIONS,
            )
            return
        self._show_settings_picker(selected_value=value)

    def _select_history_rows(self) -> None:
        """The scrollback limit, offered the way tmux's ``history-limit`` is."""
        current = prefs.history_rows()
        choices = [prefs.history_label(rows) for rows in prefs.HISTORY_CHOICES]
        self._pick_choice(
            "Scrollback",
            choices,
            prefs.history_label(current),
            self._set_history_rows,
            {
                "unlimited": "keep every row (default)",
                "500 rows": "keep the tail of the conversation only",
            },
        )

    def _set_history_rows(self, label: str) -> None:
        # The picker hands back its label, so read the number out of it.
        digits = "".join(ch for ch in label if ch.isdigit())
        rows = int(digits) if digits else 0
        prefs.set_history_rows(rows)
        self.viewport.set_history_limit(rows)
        self.viewport.touch()
        self._ok(f"scrollback set to {prefs.history_label(rows)}", topic="settings")

    def _select_thinking_lines(self, lines: str) -> None:
        set_thinking_lines(cast(ThinkingLinesOption, lines))
        label = (
            "no truncation" if lines == "none" else f"{lines} line{'s' if lines != '1' else ''}"
        )
        self._ok(f"thinking lines changed to {label}", topic="settings")


def _sync_mode_to_config(mode_id: str) -> None:
    """Sync vx's mode to the ctx config so the hint row picks it up."""
    import contextlib

    # Map vx modes to ctx permission modes
    mapping = {"ask": "prompt", "auto": "auto"}
    ctx_mode = mapping.get(mode_id, "prompt")
    with contextlib.suppress(Exception):
        set_permissions_mode(cast(CtxPermissionMode, ctx_mode))
