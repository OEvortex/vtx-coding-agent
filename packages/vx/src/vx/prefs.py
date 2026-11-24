"""vx's own preferences, kept beside the thing they configure.

ctx's config file holds what every front end shares; this holds what only vx
cares about, so a setting here cannot surprise ctx. Today that is the
transcript scrollback: how many rows vx keeps before it drops the oldest ones,
the way tmux's ``history-limit`` bounds a pane's scrollback.

Zero means keep everything, which is the default: a transcript is the record of
a session and dropping it should be a choice, not an accident.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Rows of transcript to keep. 0 disables pruning entirely.
DEFAULT_HISTORY_ROWS = 0

#: Whether the app captures the pointer for wheel scrolling. Off keeps the
#: terminal's drag-select, which is what a copy out of the transcript needs.
DEFAULT_MOUSE_CAPTURE = False

#: What ``/settings`` offers, smallest first. 0 reads as "unlimited".
HISTORY_CHOICES: tuple[int, ...] = (0, 20_000, 10_000, 5_000, 2_000, 500)


def prefs_path() -> Path:
    """Where vx keeps its own preferences."""
    try:
        from ctx.core.paths import get_config_dir

        return get_config_dir() / "vx-prefs.json"
    except Exception:  # pragma: no cover - the config dir is there in practice
        return Path.home() / ".config" / "ctx" / "vx-prefs.json"


def _read() -> dict[str, Any]:
    path = prefs_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _write(data: dict[str, Any]) -> None:
    path = prefs_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
    except OSError:
        return


def history_rows() -> int:
    """How many transcript rows to keep; 0 means all of them."""
    value = _read().get("history_rows", DEFAULT_HISTORY_ROWS)
    try:
        rows = int(value)
    except (TypeError, ValueError):
        return DEFAULT_HISTORY_ROWS
    return max(0, rows)


def set_history_rows(rows: int) -> None:
    data = _read()
    data["history_rows"] = max(0, int(rows))
    _write(data)


def mouse_capture() -> bool:
    """Whether the wheel scrolls the transcript, capturing the pointer."""
    value = _read().get("mouse_capture", DEFAULT_MOUSE_CAPTURE)
    return bool(value) if isinstance(value, bool) else DEFAULT_MOUSE_CAPTURE


def set_mouse_capture(enabled: bool) -> None:
    data = _read()
    data["mouse_capture"] = bool(enabled)
    _write(data)


def history_label(rows: int | None = None) -> str:
    """``unlimited`` or ``5,000 rows``, as the settings picker prints it."""
    value = history_rows() if rows is None else rows
    return "unlimited" if value <= 0 else f"{value:,} rows"
