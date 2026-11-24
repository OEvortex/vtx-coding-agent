"""Harness-owned runtime knobs.

The agent engine (loop, turn runner) must not depend on any product
package, so the tunables it needs live here with product-neutral
defaults. The coding agent's config loader
(:mod:`ctx.core.config`) mirrors user YAML into this object via
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
    #: Output budget for the summarization call. openclaude measured p99.99 of
    #: compact summaries at 17,387 tokens and reserves 20k, so the model is not
    #: cut off mid-handoff the way a 4096-token default would.
    compaction_summary_max_tokens: int = 20_000
    #: Recent conversation kept verbatim beside the summary. Bounds mirror
    #: openclaude's session-memory compaction (10k min, 40k hard cap).
    compaction_keep_recent_tokens: int = 12_000
    compaction_keep_max_tokens: int = 40_000
    compaction_min_text_messages: int = 5
    #: Clear old tool results before summarizing, keeping the last few intact.
    compaction_prune_tool_results: bool = True
    compaction_tool_results_to_keep: int = 3

    # Tool-call supervision
    tool_call_idle_timeout_seconds: float = 180.0


_config = HarnessConfig()


def get_harness_config() -> HarnessConfig:
    return _config


def apply_harness_settings(
    *,
    max_turns: int | None = None,
    default_context_window: int | None = None,
    compaction_threshold_percent: float | None = None,
    compaction_on_overflow: Literal["continue", "pause"] | None = None,
    compaction_summary_max_tokens: int | None = None,
    compaction_keep_recent_tokens: int | None = None,
    compaction_keep_max_tokens: int | None = None,
    compaction_min_text_messages: int | None = None,
    compaction_prune_tool_results: bool | None = None,
    compaction_tool_results_to_keep: int | None = None,
    tool_call_idle_timeout_seconds: float | None = None,
) -> None:
    """Merge user-facing settings into the harness config (None = keep)."""
    if max_turns is not None:
        _config.max_turns = max_turns
    if default_context_window is not None:
        _config.default_context_window = default_context_window
    if compaction_threshold_percent is not None:
        _config.compaction_threshold_percent = compaction_threshold_percent
    if compaction_on_overflow is not None:
        _config.compaction_on_overflow = compaction_on_overflow
    if compaction_summary_max_tokens is not None:
        _config.compaction_summary_max_tokens = compaction_summary_max_tokens
    if compaction_keep_recent_tokens is not None:
        _config.compaction_keep_recent_tokens = compaction_keep_recent_tokens
    if compaction_keep_max_tokens is not None:
        _config.compaction_keep_max_tokens = compaction_keep_max_tokens
    if compaction_min_text_messages is not None:
        _config.compaction_min_text_messages = compaction_min_text_messages
    if compaction_prune_tool_results is not None:
        _config.compaction_prune_tool_results = compaction_prune_tool_results
    if compaction_tool_results_to_keep is not None:
        _config.compaction_tool_results_to_keep = compaction_tool_results_to_keep
    if tool_call_idle_timeout_seconds is not None:
        _config.tool_call_idle_timeout_seconds = tool_call_idle_timeout_seconds
