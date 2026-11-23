"""fx color themes, ported from ``reff/src/core/shared/theme.zig``.

Two built-ins ship: ``fx-dark`` (default) and ``fx-light``. Every visual
value is an escape-slot name from the Zig theme so the Python UI can stay a
one-to-one port of fx's look.
"""

from __future__ import annotations

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
    diff_added_marker_style: str
    diff_removed_marker_style: str
    approval_button_active_style: str
    approval_button_inactive_style: str
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
    diff_added_style="\x1b[38;5;252m",
    diff_removed_style="\x1b[38;5;252m",
    diff_added_marker_style="",
    diff_removed_marker_style="",
    approval_button_active_style="\x1b[48;5;255m\x1b[38;5;235m\x1b[1m",
    approval_button_inactive_style="\x1b[48;5;239m\x1b[38;5;255m",
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
    diff_added_style="\x1b[38;5;238m",
    diff_removed_style="\x1b[38;5;238m",
    diff_added_marker_style="",
    diff_removed_marker_style="",
    approval_button_active_style="\x1b[48;5;236m\x1b[38;5;255m\x1b[1m",
    approval_button_inactive_style="\x1b[48;5;251m\x1b[38;5;237m",
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

_active: Theme = FX_DARK


def current() -> Theme:
    return _active


def is_light() -> bool:
    return _active.light


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
