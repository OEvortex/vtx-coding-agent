"""Tests for pi-parity thinking-level / reasoning-effort detection.

Covers the models.dev ``reasoning_options`` -> thinking-level-map
conversion, per-model supported-level derivation, clamping, and the
integration into dynamic model parsing.
"""

from __future__ import annotations

import pytest

from vtx.ai.dynamic_models import DynamicModelEntry, _parse_models
from vtx.ai.thinking import (
    EXTENDED_THINKING_LEVELS,
    clamp_thinking_level,
    get_supported_thinking_levels,
    parse_models_dev_reasoning_options,
)

# =============================================================================
# parse_models_dev_reasoning_options (pi's getEffortThinkingLevelMap)
# =============================================================================


def test_effort_options_convert_to_level_map():
    options = [
        {"type": "effort", "values": ["minimal", "low", "medium", "high", "none", "default"]}
    ]
    mapping = parse_models_dev_reasoning_options(options)
    assert mapping == {
        "off": "none",
        "minimal": "minimal",
        "low": "low",
        "medium": "medium",
        "high": "high",
        # Never advertised -> explicitly unsupported.
        "xhigh": None,
        "max": None,
    }


def test_toggle_style_has_no_effort_equivalent():
    assert parse_models_dev_reasoning_options([{"type": "toggle"}]) is None


def test_budget_tokens_only_yields_none():
    assert parse_models_dev_reasoning_options([{"type": "budget_tokens", "min": 1024}]) is None


def test_empty_or_missing_options_yield_none():
    assert parse_models_dev_reasoning_options(None) is None
    assert parse_models_dev_reasoning_options([]) is None


def test_none_only_efforts_still_produce_a_map():
    mapping = parse_models_dev_reasoning_options([{"type": "effort", "values": ["none"]}])
    assert mapping is not None
    assert mapping["off"] == "none"
    for level in ("minimal", "low", "medium", "high", "xhigh", "max"):
        assert mapping[level] is None


def test_null_and_default_values_are_ignored():
    options = [{"type": "effort", "values": ["low", None, "default"]}]
    mapping = parse_models_dev_reasoning_options(options)
    assert mapping is not None
    assert mapping["off"] is None  # no explicit "none"
    assert mapping["low"] == "low"


# =============================================================================
# get_supported_thinking_levels (pi's getSupportedThinkingLevels)
# =============================================================================


def test_non_reasoning_model_supports_only_off():
    assert get_supported_thinking_levels(reasoning=False) == ["off"]
    assert get_supported_thinking_levels(reasoning=False, thinking_level_map={"high": "high"}) == [
        "off"
    ]


def test_reasoning_without_map_gets_standard_levels():
    levels = get_supported_thinking_levels(reasoning=True)
    assert "off" in levels
    for level in ("minimal", "low", "medium", "high"):
        assert level in levels
    # xhigh/max are opt-in only.
    assert "xhigh" not in levels
    assert "max" not in levels


def test_map_excludes_explicitly_unsupported_levels():
    mapping = {
        "off": "none",
        "minimal": None,
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": None,
        "max": None,
    }
    levels = get_supported_thinking_levels(reasoning=True, thinking_level_map=mapping)
    assert levels == ["off", "low", "medium", "high"]


def test_xhigh_max_included_when_advertised():
    mapping = {level: level for level in EXTENDED_THINKING_LEVELS}
    levels = get_supported_thinking_levels(reasoning=True, thinking_level_map=mapping)
    assert levels == list(EXTENDED_THINKING_LEVELS)


def test_canonical_level_order_is_preserved():
    mapping = {"off": "none", "high": "high", "xhigh": "xhigh", "minimal": None}
    levels = get_supported_thinking_levels(reasoning=True, thinking_level_map=mapping)
    # minimal explicitly unsupported; unmapped standard levels stay available.
    assert levels == ["off", "low", "medium", "high", "xhigh"]


# =============================================================================
# clamp_thinking_level (pi's clampThinkingLevel)
# =============================================================================


def test_clamp_exact_match():
    assert clamp_thinking_level("high", ["off", "low", "high"]) == "high"


@pytest.mark.parametrize(
    ("level", "supported", "expected"),
    [
        ("xhigh", ["off", "high"], "high"),  # prefer lower when nothing higher
        ("low", ["off", "medium"], "medium"),  # prefer higher first
        ("bogus", ["off", "low"], "off"),  # unknown level -> lowest available
        ("high", [], "off"),  # nothing supported -> off
        ("max", ["max"], "max"),
    ],
)
def test_clamp_fallbacks(level, supported, expected):
    assert clamp_thinking_level(level, supported) == expected


# =============================================================================
# Integration: dynamic model parsing picks up the map
# =============================================================================


def test_parse_models_attaches_thinking_level_map_from_models_dev():
    raw = [{"id": "test-model", "name": "Test Model"}]
    models_dev = {
        "test-model": {
            "reasoning": True,
            "reasoning_options": [{"type": "effort", "values": ["low", "medium", "high", "none"]}],
            "limit": {"context": 128000, "output": 8192},
        }
    }
    entries = _parse_models(raw, models_dev)
    assert len(entries) == 1
    entry: DynamicModelEntry = entries[0]
    assert entry.supports_thinking is True
    assert entry.thinking_level_map is not None
    assert entry.thinking_level_map["off"] == "none"
    assert entry.thinking_level_map["high"] == "high"
    assert entry.thinking_level_map["xhigh"] is None

    # The derived map drives the supported levels end to end.
    levels = get_supported_thinking_levels(
        reasoning=entry.supports_thinking, thinking_level_map=entry.thinking_level_map
    )
    assert levels == ["off", "low", "medium", "high"]
