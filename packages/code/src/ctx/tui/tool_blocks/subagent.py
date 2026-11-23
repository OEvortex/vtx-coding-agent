"""The in-chat ``delegate_subagent`` tool block.

The scrollback keeps the *receipt* — what was dispatched, and how it ended —
and nothing else. Live counters, the spinner and per-agent activity live in
the pinned Agents panel. This block folds the Task tool's raw progress
events itself, so the generic chat log only has to forward them.
"""

from __future__ import annotations

import contextlib
import time

from ctx.core.config import config
from ctx.tui import task_ui  # ty: ignore[import-not-found]
from rich.style import Style
from rich.text import Text
from textual.widgets import Label

from .base import CodingToolBlock

class TaskToolBlock(CodingToolBlock):
    """Dispatch receipt for one sub-agent.

    The scrollback keeps the *receipt* — what was dispatched, and how it ended
    — and nothing else::

        ▸ Explore  Map public API surface
          ⎿  Running in background (ID: 623586f8-8334-468)

    Live counters, the spinner and per-agent activity live in the pinned
    Agents panel (:mod:`ctx.tui.agents_panel`), which shows every sub-agent at
    once instead of one block per dispatch. This block repaints only when a
    progress event arrives, so it costs nothing while the panel animates.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._task_live: dict = {}
        self._task_stats: dict | None = None
        self._task_finished: dict | None = None
        self._task_started: float | None = None
        self._task_elapsed_ms: float | None = None
        # A background dispatch finalizes its tool result immediately, long
        # before the sub-agent ends. This tracks the sub-agent's own end so
        # the receipt doesn't read as "finished" at 0 turns / 0 tool calls.
        self._subagent_ended: bool = False

    def on_mount(self) -> None:
        if self._success is None and not self._awaiting_approval:
            if self._task_started is None:
                self._task_started = time.monotonic()
            if self._task_stats is None:
                self._task_stats = {
                    "subagent": "subagent",
                    "turns": 0,
                    "tool_uses": 0,
                    "tokens": 0,
                }
            self._render_result_output()

    def _format_header(self, truncate: bool = True) -> Text:
        colors = config.ui.colors
        result = Text()

        stats = self._task_finished or self._task_stats or {}
        stop_label = stats.get("stop_label")
        if self._success is None:
            icon = task_ui.GLYPHS["tool_call"]
            icon_style = colors.running
            name_style = Style(color=colors.fg, bold=True)
        elif self._success is False or stop_label == "error":
            icon = task_ui.GLYPHS["failure"]
            icon_style = colors.failed
            name_style = Style(color=colors.failed, bold=True)
        elif stop_label in ("interrupted", "cancelled"):
            icon = task_ui.GLYPHS["stopped"]
            icon_style = Style(color=colors.dim)
            name_style = Style(color=colors.dim, bold=True)
        else:
            icon = task_ui.GLYPHS["success"]
            icon_style = Style(color=colors.success)
            name_style = Style(color=colors.fg, bold=True)

        subagent_name = stats.get("subagent") or self._name or "subagent"
        desc = self._call_msg or ""

        result.append(f"{icon} ", style=icon_style)
        result.append(subagent_name, style=name_style)
        if desc:
            result.append("  ")
            result.append(desc, style=Style(color=colors.dim))

        finished = self._task_finished or {}
        if self._subagent_ended and finished:
            metrics: list[str] = []
            elapsed_ms = self._current_elapsed_ms()
            if elapsed_ms is not None:
                metrics.append(task_ui.format_ms(elapsed_ms))
            tokens = finished.get("tokens")
            if tokens:
                metrics.append(task_ui.format_tokens(tokens))
            if metrics:
                result.append("  " + " · ".join(metrics), style=Style(color=colors.dim))

        return result

    def set_task_progress(self, stats: dict) -> None:
        """Fold a progress snapshot into the receipt body and header."""
        if self._task_started is None:
            self._task_started = time.monotonic()
        if stats.get("ended"):
            self._subagent_ended = True
            elapsed = stats.get("elapsed_ms")
            self._task_elapsed_ms = (
                elapsed if elapsed is not None else (time.monotonic() - self._task_started) * 1000
            )
            self._task_finished = dict(stats)
        else:
            self._task_stats = dict(stats)
        self._render_result_output()
        # Update header in case subagent name resolved
        with contextlib.suppress(Exception):
            self.query_one("#tool-header", Label).update(self._format_header())

    def set_result(
        self,
        ui_summary: str | None,
        ui_details: str | None,
        success: bool,
        markup: bool = True,
        ui_details_full: str | None = None,
        images: list | None = None,
    ) -> None:
        if (
            self._subagent_ended
            and self._task_started is not None
            and self._task_elapsed_ms is None
        ):
            self._task_elapsed_ms = (time.monotonic() - self._task_started) * 1000
        if self._subagent_ended and self._task_finished is None:
            self._task_finished = dict(self._task_stats or {})
            self._task_finished["ended"] = True
            if not success and ui_summary:
                self._task_finished["error"] = ui_summary
        super().set_result(
            ui_summary=None,
            ui_details=ui_details,
            success=success,
            markup=markup,
            ui_details_full=ui_details_full,
            images=images,
        )

    def _current_elapsed_ms(self) -> float | None:
        if self._task_elapsed_ms is not None:
            return self._task_elapsed_ms
        if self._task_started is not None:
            return (time.monotonic() - self._task_started) * 1000
        return None

    def _show_body(self, rendered: Text, *, finished: bool) -> None:
        output = self.query_one("#tool-output", Label)
        if finished:
            self.remove_class("-compact")
            self.add_class("-with-details")
        else:
            self.add_class("-compact")
            self.remove_class("-with-details")
        output.remove_class("-hidden")
        output.remove_class("-details")
        output.update(rendered)

    def _render_result_output(self) -> None:
        try:
            self.query_one("#tool-output", Label)
        except Exception:
            return

        if self._task_finished is not None and not self._awaiting_approval:
            result_text = self._ui_details or self._ui_details_full or ""
            rendered = task_ui.render_finished(
                self._task_finished,
                self._success,
                self._task_elapsed_ms,
                result_text=result_text,
                expanded=self._expanded,
            )
            self._show_body(rendered, finished=True)
            return

        # A background dispatch already returned its tool result (the
        # "Running in background (ID: …)" receipt) while the sub-agent is
        # still going. Show that receipt, not a frozen 0-turn summary.
        if self._success is not None and not self._awaiting_approval:
            super()._render_result_output()
            return

        if self._task_stats is not None and not self._awaiting_approval:
            self._show_body(task_ui.render_receipt(self._task_stats), finished=False)
            return

        super()._render_result_output()

    def apply_task_progress(self, event: dict) -> None:
        """Fold a raw ``delegate_subagent`` progress event into this receipt.

        Called by the chat log, which forwards the event to the owning block.
        The event vocabulary is defined by the Task tool's progress callback.
        """
        kind = event.get("kind")
        state = self._task_live
        if not state:
            state.update({
                "subagent": event.get("subagent", "subagent"),
                "model": None,
                "max_turns": None,
                "turns": 0,
                "tool_uses": 0,
                "tool_counts": {},
                "tokens": 0,
                "active_tool": None,
                "last_text": "",
                "final_text": "",
                "transcript": [],
                "started": time.monotonic(),
                "ended": False,
                "stop_label": None,
                "error": None,
                "queued": False,
                "queue_position": 0,
            })
        if event.get("tool_counts"):
            state["tool_counts"] = dict(event["tool_counts"])
        if event.get("final_text"):
            state["final_text"] = event["final_text"]
        if event.get("transcript"):
            state["transcript"] = list(event["transcript"])

        if kind == "subagent_queued":
            state["queued"] = True
            state["queue_position"] = event.get("position") or 0
        elif kind == "subagent_start":
            state["model"] = event.get("model")
            state["max_turns"] = event.get("max_turns")
            state["queued"] = False
            state["queue_position"] = 0
        elif kind == "text_delta":
            state["last_text"] = (state["last_text"] + event.get("delta", ""))[-400:]
        elif kind == "tool_start":
            state["tool_uses"] += 1
            state["active_tool"] = event.get("tool_name")
        elif kind == "tool_result":
            state["active_tool"] = None
        elif kind == "turn_end":
            state["turns"] = event.get("turns", state["turns"])
            state["tokens"] = event.get("tokens", state["tokens"])
        elif kind == "error":
            state["error"] = event.get("error")
        elif kind in ("interrupted", "cancelled"):
            state["error"] = state["error"] or kind
            self._end_task_state(state, stop_label=kind)
        elif kind == "subagent_end":
            self._end_task_state(state, stop_label=event.get("stop_reason"))

        # Snapshot for the renderer; internal timing key stripped.
        snapshot = {k: v for k, v in state.items() if k != "started"}
        self.set_task_progress(snapshot)

    @staticmethod
    def _end_task_state(state: dict, stop_label: str | None) -> None:
        state["ended"] = True
        state["stop_label"] = stop_label or state.get("stop_label")
        state["elapsed_ms"] = (time.monotonic() - state["started"]) * 1000
        state["queued"] = False
        state["queue_position"] = 0

