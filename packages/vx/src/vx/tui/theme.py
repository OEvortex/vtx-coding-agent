"""fx color themes, ported from ``reff/src/core/shared/theme.zig``.

Two built-ins ship: ``fx-dark`` (default) and ``fx-light``. Every visual
value is an escape-slot name from the Zig theme so the Python UI can stay a
one-to-one port of fx's look.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SyntaxPalette:
    keyword_style: str
    string_style: str
    number_style: str
    comment_style: str
    function_style: str
    variable_style: str
    operator_style: str


@dataclass(frozen=True)
class Theme:
    name: str
    light: bool

    divider_style: str
    hint_style: str
    statusline_style: str
    tag_style: str
    subtitle_style: str
    system_notice_label_style: str
    system_notice_text_style: str
    dim_style: str
    warning_style: str
    green_style: str
    red_style: str
    diff_added_style: str
    diff_removed_style: str
    diff_ctx_style: str
    diff_added_marker_style: str
    diff_removed_marker_style: str
    approval_button_active_style: str
    approval_button_inactive_style: str
    tooltip_style: str
    selected_completion_style: str
    permission_auto_style: str
    user_card_marker_style: str
    user_card_accent_style: str
    inline_code_open: str
    inline_code_close: str
    task_completed_open: str
    tool_stdout_style: str
    tool_stderr_style: str
    link_style: str
    syntax: SyntaxPalette


FX_DARK = Theme(
    name="fx-dark",
    light=False,
    divider_style="\x1b[38;5;240m",
    hint_style="\x1b[38;5;255m",
    statusline_style="\x1b[38;5;245m",
    tag_style="\x1b[1;38;5;255m",
    subtitle_style="\x1b[1;38;5;255m",
    system_notice_label_style="\x1b[1;38;5;252m",
    system_notice_text_style="\x1b[38;5;250m",
    dim_style="\x1b[38;5;245m",
    warning_style="\x1b[38;5;252m",
    green_style="\x1b[38;5;252m",
    red_style="\x1b[38;5;252m",
    diff_added_style="\x1b[38;5;113m",
    diff_removed_style="\x1b[38;5;167m",
    diff_ctx_style="\x1b[38;5;245m",
    diff_added_marker_style="\x1b[38;5;113m",
    diff_removed_marker_style="\x1b[38;5;167m",
    approval_button_active_style="\x1b[48;5;255m\x1b[38;5;235m\x1b[1m",
    approval_button_inactive_style="\x1b[48;5;239m\x1b[38;5;255m",
    tooltip_style="\x1b[48;5;24m\x1b[38;5;255m",
    selected_completion_style="\x1b[1;38;5;255m",
    permission_auto_style="\x1b[38;5;252m",
    user_card_marker_style="\x1b[38;5;255m",
    user_card_accent_style="\x1b[38;5;252m",
    inline_code_open="\x1b[38;5;255m",
    inline_code_close="\x1b[39m",
    task_completed_open="\x1b[38;5;252m",
    tool_stdout_style="\x1b[38;5;245m",
    tool_stderr_style="\x1b[38;5;252m",
    link_style="\x1b[38;5;75m",
    syntax=SyntaxPalette(
        keyword_style="\x1b[38;5;252m",
        string_style="\x1b[38;5;250m",
        number_style="\x1b[38;5;250m",
        comment_style="\x1b[38;5;245m",
        function_style="\x1b[38;5;252m",
        variable_style="\x1b[38;5;252m",
        operator_style="\x1b[38;5;252m",
    ),
)

FX_LIGHT = Theme(
    name="fx-light",
    light=True,
    divider_style="\x1b[38;5;250m",
    hint_style="\x1b[38;5;235m",
    statusline_style="\x1b[38;5;241m",
    tag_style="\x1b[1;38;5;235m",
    subtitle_style="\x1b[1;38;5;235m",
    system_notice_label_style="\x1b[1;38;5;238m",
    system_notice_text_style="\x1b[38;5;241m",
    dim_style="\x1b[38;5;247m",
    warning_style="\x1b[38;5;238m",
    green_style="\x1b[38;5;238m",
    red_style="\x1b[38;5;238m",
    diff_added_style="\x1b[38;5;22m",
    diff_removed_style="\x1b[38;5;1m",
    diff_ctx_style="\x1b[38;5;243m",
    diff_added_marker_style="\x1b[38;5;22m",
    diff_removed_marker_style="\x1b[38;5;1m",
    approval_button_active_style="\x1b[48;5;236m\x1b[38;5;255m\x1b[1m",
    approval_button_inactive_style="\x1b[48;5;251m\x1b[38;5;237m",
    tooltip_style="\x1b[48;5;194m\x1b[38;5;235m",
    selected_completion_style="\x1b[1;38;5;235m",
    permission_auto_style="\x1b[38;5;238m",
    user_card_marker_style="\x1b[38;5;235m",
    user_card_accent_style="\x1b[38;5;238m",
    inline_code_open="\x1b[38;5;235m",
    inline_code_close="\x1b[39m",
    task_completed_open="\x1b[38;5;238m",
    tool_stdout_style="\x1b[38;5;245m",
    tool_stderr_style="\x1b[38;5;252m",
    link_style="\x1b[38;5;25m",
    syntax=SyntaxPalette(
        keyword_style="\x1b[38;5;238m",
        string_style="\x1b[38;5;241m",
        number_style="\x1b[38;5;241m",
        comment_style="\x1b[38;5;243m",
        function_style="\x1b[38;5;238m",
        variable_style="\x1b[38;5;238m",
        operator_style="\x1b[38;5;238m",
    ),
)


def _fg(hex_color: str, *, bold: bool = False) -> str:
    """A 24-bit foreground SGR sequence from ``#rrggbb``."""
    value = (hex_color or "").lstrip("#")
    if len(value) != 6:
        return "\x1b[1m" if bold else ""
    try:
        r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return "\x1b[1m" if bold else ""
    prefix = "\x1b[1;38;2" if bold else "\x1b[38;2"
    return f"{prefix};{r};{g};{b}m"


def _bg(hex_color: str) -> str:
    value = (hex_color or "").lstrip("#")
    if len(value) != 6:
        return ""
    try:
        r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return ""
    return f"\x1b[48;2;{r};{g};{b}m"


def _luminance(hex_color: str) -> float:
    value = (hex_color or "").lstrip("#")
    if len(value) != 6:
        return 0.0
    try:
        r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return 0.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _filled(color: str) -> str:
    """A background fill with readable text, on either side of the brightness line."""
    background = _bg(color)
    if not background:
        return ""
    return background + (_fg("#101010") if _luminance(color) > 0.5 else _fg("#ffffff"))


def from_ctx_theme(theme_id: str) -> Theme | None:
    """Build an fx theme from one of ctx's themes, or ``None`` if unknown.

    vx keeps fx's slot names (divider, dim, selected completion...), so a ctx
    theme's colors map onto them by role. The result keeps fx's layout and
    swaps the palette, which is what ``/themes`` switches.
    """
    try:
        from ctx.core.themes import get_theme
    except Exception:
        return None
    try:
        colors = get_theme(theme_id).model_dump().get("colors") or {}
    except Exception:
        return None
    if not colors:
        return None

    def role(name: str, fallback: str) -> str:
        value = colors.get(name)
        return value if isinstance(value, str) and value else fallback

    fg = role("fg", "#e5e7eb")
    dim = role("dim", "#6b7280")
    muted = role("muted", dim)
    accent = role("accent", fg)
    title = role("title", accent)
    error = role("error", fg)
    success = role("success", role("diff_added", fg))
    notice = role("notice", muted)
    border = role("border", dim)
    syntax = colors.get("syntax") or {}
    light = _luminance(role("bg", "#000000")) > 0.5

    return Theme(
        name=theme_id,
        light=light,
        divider_style=_fg(border),
        hint_style=_fg(fg),
        statusline_style=_fg(muted),
        tag_style=_fg(fg, bold=True),
        subtitle_style=_fg(fg, bold=True),
        system_notice_label_style=_fg(title, bold=True),
        system_notice_text_style=_fg(muted),
        dim_style=_fg(muted),
        warning_style=_fg(notice),
        green_style=_fg(success),
        red_style=_fg(error),
        diff_added_style=_fg(role("diff_added", "#00c853")),
        diff_removed_style=_fg(role("diff_removed", "#ff1744")),
        diff_ctx_style=_fg(muted),
        diff_added_marker_style=_fg(role("diff_added", "#00c853")),
        diff_removed_marker_style=_fg(role("diff_removed", "#ff1744")),
        approval_button_active_style=_bg(fg) + _fg(role("bg", "#000000")) + "\x1b[1m",
        approval_button_inactive_style=_bg(border) + _fg(fg),
        tooltip_style=_filled(accent),
        selected_completion_style=_fg(role("selected", accent), bold=True),
        permission_auto_style=_fg(success),
        user_card_marker_style=_fg(accent),
        user_card_accent_style=_fg(title),
        inline_code_open=_fg(role("markdown_code", accent)),
        inline_code_close="\x1b[39m",
        task_completed_open=_fg(success),
        tool_stdout_style=_fg(muted),
        tool_stderr_style=_fg(error),
        link_style=_fg(role("info", accent)),
        syntax=SyntaxPalette(
            keyword_style=_fg(syntax.get("keyword") or accent, bold=True),
            string_style=_fg(syntax.get("string") or success),
            number_style=_fg(syntax.get("number") or notice),
            comment_style=_fg(syntax.get("comment") or muted),
            function_style=_fg(syntax.get("function") or title),
            variable_style=_fg(syntax.get("variable") or fg),
            operator_style=_fg(syntax.get("operator") or muted),
        ),
    )


def available_themes() -> list[tuple[str, str]]:
    """fx's two palettes plus every ctx theme ``/themes`` can switch to."""
    options: list[tuple[str, str]] = [(FX_DARK.name, "fx dark"), (FX_LIGHT.name, "fx light")]
    try:
        from ctx.core.themes import get_theme_options
    except Exception:
        return options
    with contextlib.suppress(Exception):
        options.extend(get_theme_options())
    return options


def apply_named(name: str) -> Theme:
    """Switch to ``fx-dark``, ``fx-light`` or a ctx theme id."""
    lowered = (name or "").strip().lower()
    if lowered in ("dark", "fx-dark", "default", ""):
        return apply_theme(FX_DARK)
    if lowered in ("light", "fx-light"):
        return apply_theme(FX_LIGHT)
    built = from_ctx_theme(lowered)
    if built is None:
        raise ValueError(f"Unknown theme: {name}")
    return apply_theme(built)


_active: Theme = FX_DARK


def current() -> Theme:
    return _active


def selected_theme() -> Theme:
    """Resolve the theme the way fx does: an explicit pin, then the terminal.

    ``VX_THEME`` / ``FX_THEME`` accept ``light``/``dark`` (or a theme name whose
    substring says which). Without a pin we follow ``COLORFGBG``, which many
    terminals export, and otherwise fall back to fx's dark default.
    """
    pin = (os.environ.get("VX_THEME") or os.environ.get("FX_THEME") or "").strip().lower()
    if pin:
        if pin in ("light", "fx-light") or pin.endswith("-light"):
            return FX_LIGHT
        return FX_DARK
    fgbg = os.environ.get("COLORFGBG", "")
    if fgbg:
        try:
            bg = int(fgbg.split(";")[-1])
        except ValueError:
            return FX_DARK
        if bg >= 7:
            return FX_LIGHT
    return FX_DARK


def apply_theme(theme: Theme | None = None) -> Theme:
    global _active
    _active = theme or selected_theme()
    return _active
