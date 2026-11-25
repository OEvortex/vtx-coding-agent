"""vx's own preferences, kept beside the thing they configure.

ctx's config file holds what every front end shares; this holds what only vx
cares about, so a setting here cannot surprise ctx. Today that is the
transcript scrollback: how many rows vx keeps before it drops the oldest ones,
the way tmux's ``history-limit`` bounds a pane's scrollback.

Zero means keep everything, which is the default: a transcript is the record of
a session and dropping it should be a choice, not an accident.

The sidebar is here for the same reason. Its *shape* -- which sections are
folded -- is a habit. Its *presence* is not a setting at all: the panel belongs
to the work, so it opens when the agent has a plan or has written a file and
closes when it has neither. A toggle (ctrl+t, ``/settings``) pins it either way,
and that pin is the habit we keep.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Rows of transcript to keep. 0 disables pruning entirely.
DEFAULT_HISTORY_ROWS = 0

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


def _flag(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def sidebar_shown() -> bool:
    """Whether a pinned panel is open. Only means anything once it is pinned."""
    return _flag(_read().get("sidebar_shown"), True)


def set_sidebar_shown(shown: bool) -> None:
    data = _read()
    data["sidebar_shown"] = bool(shown)
    data["sidebar_pinned"] = True
    _write(data)


def sidebar_pinned() -> bool:
    """Whether the user has decided the panel's fate, or left it to the work."""
    return _flag(_read().get("sidebar_pinned"), False)


def set_sidebar_pinned(pinned: bool) -> None:
    data = _read()
    data["sidebar_pinned"] = bool(pinned)
    _write(data)


def sidebar_folds() -> tuple[bool, bool]:
    """``(plan_open, files_open)``: a folded section stays folded."""
    data = _read()
    return (
        _flag(data.get("sidebar_plan_open"), True),
        _flag(data.get("sidebar_files_open"), True),
    )


def set_sidebar_folds(plan_open: bool, files_open: bool) -> None:
    data = _read()
    data["sidebar_plan_open"] = bool(plan_open)
    data["sidebar_files_open"] = bool(files_open)
    _write(data)


def sidebar_label() -> str:
    """``auto``, or ``on``/``off`` plus what is folded."""
    if not sidebar_pinned():
        return "auto"
    if not sidebar_shown():
        return "off"
    plan_open, files_open = sidebar_folds()
    if plan_open and files_open:
        return "on"
    folded = [name for name, open_ in (("plan", plan_open), ("files", files_open)) if not open_]
    return f"on · {', '.join(folded)} folded"


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


def history_label(rows: int | None = None) -> str:
    """``unlimited`` or ``5,000 rows``, as the settings picker prints it."""
    value = history_rows() if rows is None else rows
    return "unlimited" if value <= 0 else f"{value:,} rows"
