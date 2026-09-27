"""Thinking-level / reasoning-effort detection.

The **models.dev** catalog publishes two fields per model:

- ``reasoning``: whether the model supports reasoning at all.
- ``reasoning_options``: verified reasoning controls, e.g.
  ``{"type": "effort", "values": ["minimal", "low", "medium", "high",
  "xhigh", "max", "none", "default"]}``, ``{"type": "toggle"}`` or
  ``{"type": "budget_tokens", ...}``.

This module converts those into a *thinking level map*
(``level -> provider effort string | None`` where ``None`` marks a level
as explicitly unsupported) and derives/clamps the levels a model
actually supports:

- :func:`parse_models_dev_reasoning_options` — models.dev
  ``reasoning_options`` to a thinking-level map.
- :func:`get_supported_thinking_levels` — which of the canonical levels a
  model supports.
- :func:`resolve_thinking_levels` — which levels to *offer* for a given
  model + transport (the single source of truth for every selector).
- :func:`clamp_thinking_level` — nearest-available fallback when a saved
  or requested level isn't supported by the selected model.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Canonical level vocabulary, ordered low-to-high.
EXTENDED_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

_EFFORT_LEVELS = EXTENDED_THINKING_LEVELS[1:]  # minimal..max

# The same vocabulary as spelled in vtx's own surfaces (picker entries, provider
# ``thinking_levels``, session state): the catalog's "off" is called "none"
# because that is what providers call ``reasoning_effort: "none"``. Every
# translation between the two spellings goes through ``_as_catalog_level`` so
# the two vocabularies can never drift apart again.
THINKING_LEVELS: tuple[str, ...] = ("none", *_EFFORT_LEVELS)

_MISSING = object()  # sentinel: key absent from the map (vs. explicit None)


def _as_catalog_level(level: str) -> str:
    """Normalize the off-level spelling (``"none"`` -> ``"off"``)."""
    return "off" if level == "none" else level


def parse_models_dev_reasoning_options(
    options: Iterable[Any] | None, *, max_tokens: int | None = None
) -> dict[str, str | None] | None:
    """Convert models.dev ``reasoning_options`` into a thinking-level map.

    Two styles are convertible, and effort wins when a model publishes both
    (it is the control the modern wire format actually accepts):

    - ``{"type": "effort", "values": [...]}`` — named effort tiers.
    - ``{"type": "budget_tokens", "min": N}`` — a token budget instead of
      named efforts (Claude Haiku/Sonnet 4.5). Derived into the shared
      :data:`ANTHROPIC_BUDGETS` ladder, the same move opencode makes when it
      turns a budget range into variants.

    ``{"type": "toggle"}`` has no per-level equivalent in vtx and yields
    ``None`` (the model then keeps its own default). Values without a
    canonical level equivalent (``"default"``, JSON ``null``, unknown strings)
    are ignored.

    Returns ``{level: effort | budget | None}`` or ``None`` when nothing useful
    was found. ``None`` values mark explicitly unsupported levels; ``off``
    maps to ``"none"`` only when the model advertises it.
    """
    if not options:
        return None

    options = list(options)
    supported: set[str] = set()
    for option in options:
        if isinstance(option, dict) and option.get("type") == "effort":
            for value in option.get("values") or []:
                if value is not None:
                    supported.add(str(value))

    if any(level in supported for level in _EFFORT_LEVELS) or "none" in supported:
        mapping: dict[str, str | None] = {"off": "none" if "none" in supported else None}
        for level in _EFFORT_LEVELS:
            mapping[level] = level if level in supported else None
        return mapping

    budget = next(
        (o for o in options if isinstance(o, dict) and o.get("type") == "budget_tokens"), None
    )
    if budget is None:
        return None
    return _budget_level_map(budget, max_tokens=max_tokens)


def _budget_level_map(
    option: Mapping[str, Any], *, max_tokens: int | None
) -> dict[str, str | None]:
    """A token-budget thinking control expressed as a thinking-level map.

    Levels outside the model's budget range map to ``None`` so they are never
    offered; the ladder itself comes from :data:`ANTHROPIC_BUDGETS` so the
    wire layer and the picker agree on what a level costs in tokens.
    """
    minimum = int(option.get("min") or _ANTHROPIC_MIN_BUDGET)
    ceiling = int(option.get("max") or 0)
    if max_tokens:
        # budget_tokens must stay strictly below max_tokens.
        ceiling = min(ceiling or max_tokens, max_tokens - _ANTHROPIC_MIN_BUDGET)

    mapping: dict[str, str | None] = {"off": "none"}
    for level in _EFFORT_LEVELS:
        budget = ANTHROPIC_BUDGETS[level]
        usable = budget >= max(minimum, _ANTHROPIC_MIN_BUDGET) and (
            not ceiling or budget < ceiling
        )
        mapping[level] = budget_level(budget) if usable else None
    return mapping


def budget_level(tokens: int) -> str:
    """Encode a thinking-token budget as a thinking-level-map value."""
    return f"{_BUDGET_PREFIX}{tokens}"


def is_budget_level(value: Any) -> bool:
    """Whether a thinking-level-map value encodes a token budget."""
    return isinstance(value, str) and value.startswith(_BUDGET_PREFIX)


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
    """Nearest available level: prefer equal, then higher, then lower.

    Accepts both spellings of the off level and answers in the spelling used by
    ``supported``, so a clamped value can be handed straight back to a provider
    (whose ``thinking_levels`` say ``"none"``) without a second translation.
    """
    supported_list = list(supported)
    if not supported_list:
        return "off"
    canonical = [_as_catalog_level(lvl) for lvl in supported_list]
    target = _as_catalog_level(level)
    if target in canonical:
        return supported_list[canonical.index(target)]
    try:
        idx = EXTENDED_THINKING_LEVELS.index(target)
    except ValueError:
        return supported_list[0]
    # Prefer the next level up, then walk back down to the lowest available.
    for candidate in (
        *EXTENDED_THINKING_LEVELS[idx + 1 :],
        *reversed(EXTENDED_THINKING_LEVELS[:idx]),
    ):
        if candidate in canonical:
            return supported_list[canonical.index(candidate)]
    return supported_list[0]


def resolve_thinking_levels(
    *,
    reasoning: bool,
    thinking_level_map: Mapping[str, str | None] | None = None,
    provider_levels: Iterable[str] | None = None,
    style: str | None = None,
) -> list[str]:
    """The levels vtx should *offer* for one (model, transport) pair.

    Single source of truth for the ``/thinking`` picker, the ``ctrl+t`` cycle,
    session restore and every other selector. The rule is the same one
    opencode uses for its model variants: a control is offered only when we
    know it can actually be sent, so a level can never be offered that the
    transport rejects or silently drops on the wire.

    - Non-reasoning models: ``["none"]`` (there is nothing to turn on).
    - Catalog-verified models: exactly the levels ``reasoning_options``
      advertises, with levels the catalog marks unsupported dropped, then
      narrowed twice: to what ``provider_levels`` (the transport's effort enum)
      can express, and to what ``style`` has a wire spelling for — a token
      budget only exists on the Anthropic Messages API, so a budget-only model
      served over OpenAI-compatible endpoints falls back to ``["default"]``.
    - Reasoning models with no verified effort metadata: ``["default"]`` —
      the model keeps its own default instead of us guessing an effort it
      might 400 on. ``"default"`` resolves to "send no reasoning param".
    """
    if not reasoning:
        return ["none"]

    if thinking_level_map is None:
        return ["default"]

    derived = get_supported_thinking_levels(reasoning=True, thinking_level_map=thinking_level_map)
    if not derived:
        return ["default"]

    wire = [_as_catalog_level(lvl) for lvl in (provider_levels or THINKING_LEVELS)]
    offered = [
        "none" if lvl == "off" else lvl
        for lvl in derived
        if lvl in wire and _is_sendable(lvl, thinking_level_map, style)
    ]
    return offered or ["default"]


def _is_sendable(level: str, level_map: Mapping[str, str | None], style: str | None) -> bool:
    """Whether ``style`` has a wire spelling for this level."""
    return not (is_budget_level(level_map.get(level)) and style != ANTHROPIC_MESSAGES)


# =============================================================================
# Unified wire translation — the single home for effort -> wire params
# =============================================================================

# Protocol families (per-API wire protocols).
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

# Marker prefix for thinking-level-map values that carry a token budget instead
# of a named effort (``"budget:8192"``). Only the Anthropic wire format has a
# spelling for it; every other transport drops such a level rather than
# forwarding the marker.
_BUDGET_PREFIX = "budget:"


def _resolve_effort(
    level: str,
    *,
    level_map: Mapping[str, str | None] | None,
    default_when_unmapped: str | None = None,
) -> str | None:
    """Resolve ``level`` through the map.

    Returns the mapped string, the level itself when unmapped, or ``None``
    when explicitly unsupported.
    """
    mapped = (level_map or {}).get(level)
    if mapped is None and level in (level_map or {}):
        return None
    # A token budget is an Anthropic-only control: no other wire format has a
    # spelling for it, so drop the level instead of forwarding the marker.
    if is_budget_level(mapped):
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
    ``level`` of ``None``/``"none"``/``"off"``/``"default"`` means "model default"
    and resolves to ``{}``. An explicit ``None`` in ``level_map`` marks the
    level unsupported and also resolves to ``{}``.
    """
    if level is None or level in ("none", "off", "default"):
        return {}

    if style == ANTHROPIC_MESSAGES:
        # Three paths:
        # 1) Catalog-verified effort (Claude 4.6+ / 4.7+): adaptive thinking +
        #    output_config.effort. minimal maps to low; xhigh/max pass through
        #    only when the catalog verifies them (level_map contains them).
        #    Docs: platform.claude.com — thinking:{type:"adaptive"} +
        #    output_config:{effort: low|medium|high|xhigh|max} replaces the
        #    deprecated budget_tokens form (400 on 4.7+).
        # 2) Catalog-verified token budget (Claude Haiku/Sonnet 4.5): the map
        #    carries ``budget:<tokens>`` and the enabled/budget_tokens form is
        #    the only thing the API accepts.
        # 3) Legacy budget path: thinking:{type:"enabled", budget_tokens:N}
        #    when no catalog map is present (keeps existing tests passing).
        if level_map is not None and level in level_map:
            mapped_val = level_map[level]
            if mapped_val is None:
                return {}
            if is_budget_level(mapped_val):
                budget = int(mapped_val.removeprefix(_BUDGET_PREFIX))
                if max_tokens:
                    budget = min(
                        budget, max(_ANTHROPIC_MIN_BUDGET, max_tokens - _ANTHROPIC_MIN_BUDGET)
                    )
                return {"thinking": {"type": "enabled", "budget_tokens": budget}}
            if isinstance(mapped_val, str):
                effort = mapped_val
                if effort == "minimal":
                    effort = "low"
                if effort in ("low", "medium", "high", "xhigh", "max"):
                    return {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}
                # Unknown mapped string — fall through to budget path
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
        # Chat Completions: reasoning_effort supports none|minimal|low|medium|high|xhigh|max
        # (docs: platform.openai.com/docs/api-reference/chat/create). "off"/"none" already
        # returned {} above; every other verified effort passes through. "max" is now valid
        # for gpt-5.6 family (was previously dropped).
        if effort in ("off",):
            return {}
        return {"reasoning_effort": effort}
    if style == OPENAI_RESPONSES:
        if effort == "off":
            return {}
        return {"reasoning": {"effort": effort}}

    raise ValueError(f"Unknown reasoning style: {style!r}")
