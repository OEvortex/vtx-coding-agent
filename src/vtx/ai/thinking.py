"""Thinking-level / reasoning-effort detection — pi parity.

Pi derives per-model reasoning controls from the **models.dev** catalog,
which publishes two fields per model:

- ``reasoning``: whether the model supports reasoning at all.
- ``reasoning_options``: verified reasoning controls, e.g.
  ``{"type": "effort", "values": ["minimal", "low", "medium", "high",
  "xhigh", "max", "none", "default"]}``, ``{"type": "toggle"}`` or
  ``{"type": "budget_tokens", ...}``.

This module converts those into a pi-style *thinking level map*
(``level -> provider effort string | None`` where ``None`` marks a level
as explicitly unsupported) and derives/clamps the levels a model
actually supports:

- :func:`parse_models_dev_reasoning_options` — models.dev
  ``reasoning_options`` to a thinking-level map.
- :func:`get_supported_thinking_levels` — which of the canonical levels a
  model supports.
- :func:`clamp_thinking_level` — nearest-available fallback when a saved
  or requested level isn't supported by the selected model.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Canonical level vocabulary, ordered low-to-high (pi's EXTENDED_THINKING_LEVELS).
EXTENDED_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

_EFFORT_LEVELS = EXTENDED_THINKING_LEVELS[1:]  # minimal..max

_MISSING = object()  # sentinel: key absent from the map (vs. explicit None)


def parse_models_dev_reasoning_options(
    options: Iterable[Any] | None,
) -> dict[str, str | None] | None:
    """Convert models.dev ``reasoning_options`` into a thinking-level map.

    Only ``{"type": "effort", "values": [...]}`` entries carry effort
    information; toggle/budget-token styles have no effort equivalent and
    yield ``None`` (the caller then falls back to provider defaults).
    Values without a canonical level equivalent (``"default"``, JSON
    ``null``, unknown strings) are ignored.

    Returns ``{level: effort | None}`` or ``None`` when nothing useful was
    found. ``None`` values mark explicitly unsupported levels; ``off``
    maps to ``"none"`` only when the model advertises it.
    """
    if not options:
        return None

    supported: set[str] = set()
    for option in options:
        if isinstance(option, dict) and option.get("type") == "effort":
            for value in option.get("values") or []:
                if value is not None:
                    supported.add(str(value))

    if not any(level in supported for level in _EFFORT_LEVELS) and "none" not in supported:
        return None

    mapping: dict[str, str | None] = {"off": "none" if "none" in supported else None}
    for level in _EFFORT_LEVELS:
        mapping[level] = level if level in supported else None
    return mapping


def get_supported_thinking_levels(
    *, reasoning: bool, thinking_level_map: Mapping[str, str | None] | None = None
) -> list[str]:
    """Levels a model supports, given its metadata.

    - Non-reasoning models support only ``"off"``.
    - Reasoning models without a map get every standard level except the
      opt-in ``xhigh``/``max`` tiers.
    - With a map, levels explicitly marked ``None`` are excluded and
      ``xhigh``/``max`` appear only when the model advertises them.
    """
    if not reasoning:
        return ["off"]

    m = thinking_level_map or {}
    out: list[str] = []
    for level in EXTENDED_THINKING_LEVELS:
        mapped = m.get(level, _MISSING)
        if mapped is None:
            continue
        if level in ("xhigh", "max"):
            if mapped is not _MISSING:
                out.append(level)
            continue
        out.append(level)
    return out


def clamp_thinking_level(level: str, supported: Iterable[str]) -> str:
    """Nearest available level: prefer equal, then higher, then lower."""
    supported_list = list(supported)
    if not supported_list:
        return "off"
    if level in supported_list:
        return level
    try:
        idx = EXTENDED_THINKING_LEVELS.index(level)
    except ValueError:
        return supported_list[0]
    for i in range(idx + 1, len(EXTENDED_THINKING_LEVELS)):
        if EXTENDED_THINKING_LEVELS[i] in supported_list:
            return EXTENDED_THINKING_LEVELS[i]
    for i in range(idx - 1, -1, -1):
        if EXTENDED_THINKING_LEVELS[i] in supported_list:
            return EXTENDED_THINKING_LEVELS[i]
    return supported_list[0]
