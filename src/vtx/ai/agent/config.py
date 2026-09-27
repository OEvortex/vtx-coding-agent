"""Harness-owned runtime knobs.

The agent engine (loop, turn runner) must not depend on any product
package, so the tunables it needs live here with product-neutral
defaults. The coding agent's config loader
(:mod:`vtx.coding_agent.config`) mirrors user YAML into this object via
:func:`apply_harness_settings`, so end-user configuration still drives
behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class HarnessConfig:
    """Runtime tunables read by the agent engine."""

    # Turn engine
    max_turns: int = 500

    # Context management
    default_context_window: int = 200_000
    compaction_threshold_percent: float = 80.0
    compaction_on_overflow: Literal["continue", "pause"] = "continue"

    # Tool-call supervision
    tool_call_idle_timeout_seconds: float = 180.0

    # Continual-harness refinement (prime parity). The loop runs a cheap
    # review gate at the ``turn_interval`` / ``compact`` boundaries and only
    # plans+applies edits when the gate approves them.
    auto_refine_enabled: bool = True
    auto_refine_turn_interval: int = 25
    auto_refine_on_compact: bool = True
    auto_refine_cooldown_seconds: float = 1200.0


_config = HarnessConfig()


def get_harness_config() -> HarnessConfig:
    return _config


def set_harness_config(config: HarnessConfig) -> None:
    global _config
    _config = config


def apply_harness_settings(
    *,
    max_turns: int | None = None,
    default_context_window: int | None = None,
    compaction_threshold_percent: float | None = None,
    compaction_on_overflow: str | None = None,
    tool_call_idle_timeout_seconds: float | None = None,
    auto_refine_enabled: bool | None = None,
    auto_refine_turn_interval: int | None = None,
    auto_refine_on_compact: bool | None = None,
    auto_refine_cooldown_seconds: float | None = None,
) -> None:
    """Merge user-facing settings into the harness config (None = keep)."""
    if max_turns is not None:
        _config.max_turns = max_turns
    if default_context_window is not None:
        _config.default_context_window = default_context_window
    if compaction_threshold_percent is not None:
        _config.compaction_threshold_percent = compaction_threshold_percent
    if compaction_on_overflow is not None:
        _config.compaction_on_overflow = compaction_on_overflow  # type: ignore
    if tool_call_idle_timeout_seconds is not None:
        _config.tool_call_idle_timeout_seconds = tool_call_idle_timeout_seconds
    if auto_refine_enabled is not None:
        _config.auto_refine_enabled = auto_refine_enabled
    if auto_refine_turn_interval is not None:
        _config.auto_refine_turn_interval = max(1, auto_refine_turn_interval)
    if auto_refine_on_compact is not None:
        _config.auto_refine_on_compact = auto_refine_on_compact
    if auto_refine_cooldown_seconds is not None:
        _config.auto_refine_cooldown_seconds = max(0.0, auto_refine_cooldown_seconds)
