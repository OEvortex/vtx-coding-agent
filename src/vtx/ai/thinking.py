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


# =============================================================================
# Unified wire translation — the single home for effort -> wire params
# =============================================================================

# Protocol families (mirrors pi's api layer and opencode's protocols).
OPENAI_COMPLETIONS = "openai-completions"  # top-level ``reasoning_effort``
OPENAI_RESPONSES = "openai-responses"  # ``reasoning: {effort: ...}``
ANTHROPIC_MESSAGES = "anthropic-messages"  # ``thinking: {type, budget_tokens}``

# Anthropic budget_tokens per level (minimum accepted is 1024; the value must
# stay strictly below max_tokens — enforced when ``max_tokens`` is provided).
ANTHROPIC_BUDGETS: dict[str, int] = {
    "minimal": 1024,
    "low": 2048,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
    "max": 32768,
}

_ANTHROPIC_MIN_BUDGET = 1024


def _resolve_effort(
    level: str,
    *,
    level_map: Mapping[str, str | None] | None,
    default_when_unmapped: str | None = None,
) -> str | None:
    """Resolve ``level`` through the map (pi semantics).

    Returns the mapped string, the level itself when unmapped, or ``None``
    when explicitly unsupported.
    """
    mapped = (level_map or {}).get(level)
    if mapped is None and level in (level_map or {}):
        return None
    if isinstance(mapped, str):
        return mapped
    return default_when_unmapped


def resolve_reasoning_params(
    style: str,
    level: str | None,
    *,
    level_map: Mapping[str, str | None] | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Translate a thinking level into wire params for the target protocol.

    This is the single translation point for every transport — the SDK
    layers call it instead of hand-rolling their own dispatch.

    - ``openai-completions``: ``{"reasoning_effort": <effort>}``
    - ``openai-responses``:   ``{"reasoning": {"effort": <effort>}}``
    - ``anthropic-messages``: ``{"thinking": {"type": "enabled",
      "budget_tokens": N}}`` where N comes from the shared budget table,
      clamped so it stays strictly below ``max_tokens``.

    ``level`` of ``None``/``"none"``/``"off"`` means "model default" and
    resolves to ``{}``. An explicit ``None`` in ``level_map`` marks the
    level unsupported and also resolves to ``{}``.
    """
    if level is None or level in ("none", "off"):
        return {}

    if style == ANTHROPIC_MESSAGES:
        # Budget-based extended thinking is the documented form across
        # current Claude models; level_map strings don't apply here.
        budget = ANTHROPIC_BUDGETS.get(level)
        if budget is None:
            return {}
        if max_tokens:
            budget = min(budget, max(_ANTHROPIC_MIN_BUDGET, max_tokens - _ANTHROPIC_MIN_BUDGET))
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}

    effort = _resolve_effort(
        level, level_map=level_map, default_when_unmapped=None if level == "none" else level
    )
    if effort is None:
        return {}

    if style == OPENAI_COMPLETIONS:
        # Chat Completions has no documented "off"/"max" switch.
        if effort in ("off", "max"):
            return {}
        return {"reasoning_effort": effort}
    if style == OPENAI_RESPONSES:
        if effort == "off":
            return {}
        return {"reasoning": {"effort": effort}}

    raise ValueError(f"Unknown reasoning style: {style!r}")
