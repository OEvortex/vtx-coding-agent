"""RunConfig — per-run knobs surfaced by the SDK."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .permissions import PermissionPolicy


@dataclass
class RunConfig:
    """Per-run configuration passed to :class:`Runner.run` / ``run_sync`` / ``run_streamed``.

    All fields are optional. Anything you don't set falls back to the
    agent's own defaults, then to Vtx's global config.
    """

    max_turns: int | None = None
    """Maximum number of model turns (LLM calls) before the run stops."""

    session_input_callback: Callable[[list[Any], list[Any]], list[Any]] | None = None
    """Optional callback that customizes how session history and new
    input are merged before each model call.

    Receives ``(history, new_input)`` and returns the final list of input
    items the model sees. The SDK persists only the new-turn items.
    """

    session_settings: Any = None
    """Per-run override for the session's ``SessionSettings`` (e.g. ``limit``)."""

    tracing_disabled: bool = False
    """Disable the default trace for this single run."""

    trace_include_sensitive_data: bool = True
    """Whether the trace captures the inputs/outputs of LLM calls and tool calls."""

    permission_policy: PermissionPolicy | None = None
    """Override the SDK's default permission policy for this run."""

    nest_handoff_history: bool = False
    """Opt-in: collapse prior transcript into a single summary block on handoff."""

    custom: dict[str, Any] = field(default_factory=dict)
    """Free-form custom data attached to the run."""
