"""Vtx mode adapter — wraps vtx.loop.Agent for use from the WebUI WebSocket channel.

When the WebUI is in "vtx" mode, the WebSocket channel routes messages here
instead of the normal AgentLoop. This adapter:

1. Creates a vtx.loop.Agent with the 11 core tools and vtx system prompt.
2. Runs one turn via ``agent.run(query)``.
3. Translates each vtx ``Event`` into the WebSocket JSON events the frontend
   already understands (delta, reasoning_delta, stream_end, turn_end, etc.).
4. Sends those events to all subscribers of the chat_id.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from loguru import logger

from vtx_claw._vtx_bridge import _ClawProviderAdapter as ClawProviderAsVtxProvider


# ---------------------------------------------------------------------------
# Number of turns VTX mode runs before stopping (tool-use loop limit).
# VTX core defaults to 10 internally; we match that.
# ---------------------------------------------------------------------------
_VTX_MAX_TURNS = 10


class VtxModeHandler:
    """Runs one user turn through the VTX core agent loop.

    Keeps a lightweight ``vtx.loop.Agent`` alive for the duration of the turn,
    translates vtx events into WebSocket JSON frames, and sends them to every
    subscriber of the given ``chat_id``.

    Usage from ``WebSocketChannel``::

        handler = VtxModeHandler(channel_config, workspace)
        handler.cancel(chat_id)          # mid-turn stop
        await handler.handle_turn(
            chat_id=...,
            content=...,
            media_paths=...,
            metadata=...,
        )
    """

    def __init__(
        self, workspace: Path, config: Any | None = None, send_to_chat: Any | None = None
    ) -> None:
        self._workspace = workspace
        self._config = config
        # ``send_to_chat`` is a callable ``(chat_id, event_type, **kwargs)``
        # that sends a JSON event to every subscriber of that chat_id.
        # Set by ``WebSocketChannel`` after construction.
        self.send_to_chat: Any = send_to_chat

        # Per-chat-id cancel event so a ``/stop`` request can cut a running turn.
        self._cancel_events: dict[str, asyncio.Event] = {}

        # Per-chat-id wall-clock counter for turn_id / turn_seq
        self._turn_seq: dict[str, int] = {}

    # -- Public API ---------------------------------------------------------

    async def handle_turn(
        self,
        chat_id: str,
        content: str,
        media_paths: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Run a single VTX-mode turn and stream results to subscribers.

        This is a coroutine that may run for many seconds (multiple LLM
        calls + tool execution rounds). The caller should wrap it in a task.
        """
        cancel_event = asyncio.Event()
        self._cancel_events[chat_id] = cancel_event

        seq = self._turn_seq.get(chat_id, 0) + 1
        self._turn_seq[chat_id] = seq
        turn_id = f"vtx-{chat_id[:8]}-{seq}"

        try:
            # -- 1. Signal turn start --------------------------------------
            self._send(chat_id, "goal_status", status="running")

            # -- 2. Build vtx Agent ----------------------------------------
            agent = await self._build_agent()

            # -- 3. Run the agent loop & translate events ------------------
            turn_wall = time.time()

            # We collect text as it streams, then finalize on stream_end/turn_end
            turn_text: list[str] = []
            turn_reasoning: list[str] = []

            # Inner try/except guarantees that ``turn_end`` is always sent,
            # even if ``agent.run()`` raises (auth error, model error, etc.).
            # Without it, the WebUI's streaming spinner hangs permanently.
            try:
                async for event in agent.run(query=content, cancel_event=cancel_event):
                    await self._translate_event(
                        chat_id, event, turn_id, seq, turn_text, turn_reasoning
                    )
            except BaseException:
                # Flush any partial text we collected so the user sees it.
                error_text = _collect_text(turn_text)
                if error_text:
                    self._send(
                        chat_id, "stream_end", turn_id=turn_id, turn_seq=seq, text=error_text
                    )
                    self._send(
                        chat_id,
                        "message",
                        content=error_text,
                        turn_id=turn_id,
                        turn_seq=seq,
                        kind="message",
                        role="assistant",
                    )
                # Signal turn end so the WebUI clears its loading spinner.
                self._send(chat_id, "turn_end", turn_id=turn_id, turn_seq=seq, error="true")
                raise

            elapsed = time.time() - turn_wall
            logger.info(
                "vtx turn {turn_id} done in {elapsed:.1f}s ({reasoning_len}r / {text_len}t)",
                turn_id=turn_id,
                elapsed=elapsed,
                reasoning_len=sum(len(r) for r in turn_reasoning),
                text_len=sum(len(t) for t in turn_text),
            )

        except asyncio.CancelledError:
            self._send(chat_id, "turn_end", turn_id=turn_id, turn_seq=seq)
            logger.info("vtx turn {turn_id} cancelled", turn_id=turn_id)
        except Exception as exc:
            self._send(chat_id, "turn_end", turn_id=turn_id, turn_seq=seq)
            logger.exception("vtx turn {turn_id} failed", turn_id=turn_id)
            self._send(chat_id, "error", detail="vtx_error", reason=str(exc))
        except BaseException:
            self._send(chat_id, "turn_end", turn_id=turn_id, turn_seq=seq)
            logger.exception("vtx turn {turn_id} failed unexpectedly", turn_id=turn_id)
        finally:
            self._send(chat_id, "goal_status", status="idle")
            self._cancel_events.pop(chat_id, None)

    def cancel(self, chat_id: str) -> None:
        """Signal the running VTX turn for ``chat_id`` to stop."""
        ev = self._cancel_events.get(chat_id)
        if ev is not None:
            ev.set()

    async def _translate_event(
        self,
        chat_id: str,
        event: Any,
        turn_id: str,
        seq: int,
        turn_text: list[str],
        turn_reasoning: list[str],
    ) -> None:
        """Translate one vtx ``Event`` into a WebSocket frame and send it.

        Also accumulates text and reasoning so ``turn_end`` can be finalised
        gracefully if the stream aborts mid-turn.
        """
        ev_type = _event_type(event)

        if ev_type == "thinking_delta":
            turn_reasoning.append(event.delta)
            self._send(chat_id, "reasoning_delta", text=event.delta, turn_id=turn_id, turn_seq=seq)

        elif ev_type == "thinking_end":
            if event.thinking:
                self._send(chat_id, "reasoning_end", turn_id=turn_id, turn_seq=seq)

        elif ev_type == "text_delta":
            turn_text.append(event.delta)
            self._send(chat_id, "delta", text=event.delta, turn_id=turn_id, turn_seq=seq)

        elif ev_type == "text_end":
            full_text = event.text
            self._send(chat_id, "stream_end", turn_id=turn_id, turn_seq=seq, text=full_text)
            self._send(
                chat_id,
                "message",
                content=full_text,
                turn_id=turn_id,
                turn_seq=seq,
                kind="message",
                role="assistant",
            )

        elif ev_type == "tool_start":
            self._send(
                chat_id,
                "file_edit",
                phase="start",
                tool=event.tool_name,
                call_id=event.tool_call_id,
                turn_id=turn_id,
                turn_seq=seq,
            )

        elif ev_type == "tool_result":
            result_text = _tool_result_text(event.result)
            self._send(
                chat_id,
                "file_edit",
                phase="end",
                tool=event.tool_name,
                call_id=event.tool_call_id,
                summary=result_text,
                turn_id=turn_id,
                turn_seq=seq,
            )

        elif ev_type == "turn_end":
            # Finalise text (e.g. a tool-only turn that never emitted text_end).
            full_text = _collect_text(turn_text)
            if full_text:
                self._send(chat_id, "stream_end", turn_id=turn_id, turn_seq=seq, text=full_text)
                self._send(
                    chat_id,
                    "message",
                    content=full_text,
                    turn_id=turn_id,
                    turn_seq=seq,
                    kind="message",
                    role="assistant",
                )
            context_tokens = _estimate_context_tokens(event)
            context_window = _estimate_context_window()
            self._send(
                chat_id,
                "turn_end",
                turn_id=turn_id,
                turn_seq=seq,
                context_tokens=context_tokens,
                context_window=context_window,
            )

        elif ev_type == "agent_end":
            self._send(chat_id, "goal_status", status="idle")

        elif ev_type == "error":
            self._send(chat_id, "error", detail="vtx_error", reason=event.error)

        elif ev_type == "interrupted":
            self._send(chat_id, "goal_status", status="idle")

        # All other events (compaction, goal evaluation, etc.) are ignored
        # — the WebUI doesn't need them for VTX mode.

    # -- Internal helpers ---------------------------------------------------

    async def _build_agent(self) -> Any:
        """Create a ``vtx.loop.Agent`` with the 11 core tools & vtx system prompt."""
        import vtx.config as vtx_config_module
        from vtx.context import Context
        from vtx.goal import GoalManager
        from vtx.loop import Agent, AgentConfig
        from vtx.prompts import build_system_prompt
        from vtx.session import Session
        from vtx.tools import DEFAULT_TOOLS, get_tools_with_extensions

        # -- 1. Get the provider -------------------------------------------
        provider = await self._get_vtx_provider()

        # -- 2. Load tools -------------------------------------------------
        tools = get_tools_with_extensions(DEFAULT_TOOLS)

        # -- 3. Session (vtx native format, stored separately) -------------
        vtx_dir = self._workspace / "vtx_sessions"
        vtx_dir.mkdir(parents=True, exist_ok=True)
        session_path = vtx_dir / "default.jsonl"
        cwd = os.getcwd()
        session = Session(session_id="default", cwd=cwd, session_file=session_path)

        # -- 4. System prompt ----------------------------------------------
        context = Context.load(cwd)
        system_prompt = build_system_prompt(cwd, context, tools=tools)

        # -- 5. Agent config -----------------------------------------------
        # LLMConfig does not expose per-field context_window/max_output_tokens;
        # those values come from the provider preset at runtime, so we leave
        # AgentConfig unbound here and let vtx fill in defaults.
        config = AgentConfig()

        agent = Agent(
            provider=provider,
            tools=tools,
            session=session,
            cwd=cwd,
            context=context,
            system_prompt=system_prompt,
            config=config,
            goal_manager=GoalManager(max_objective_chars=4000, max_turns_default=_VTX_MAX_TURNS),
        )
        return agent

    async def _get_vtx_provider(self) -> Any:
        """Create a vtx-compatible provider by wrapping a claw provider."""
        from vtx_claw.config import load_config as get_claw_config
        from vtx_claw.providers.factory import _make_provider_core

        # Always load the full claw Config (which has resolve_preset and model
        # resolution helpers).  The channel passes a WebSocketConfig here, which
        # lacks those attributes and triggers AttributeError.
        claw_cfg = get_claw_config()
        model_name = getattr(claw_cfg, "model", None) or "claude-sonnet-4-20250514"

        claw_provider = _make_provider_core(claw_cfg, model=model_name)
        vtx_provider: Any = ClawProviderAsVtxProvider(claw_provider, model=model_name)  # type: ignore[call-arg]
        return vtx_provider

    def _send(self, chat_id: str, event_type: str, **kwargs: Any) -> None:
        """Send a JSON event to all subscribers of ``chat_id``."""
        if self.send_to_chat is not None:
            self.send_to_chat(chat_id, event_type, **kwargs)


# ---------------------------------------------------------------------------
# Event translation helpers
# ---------------------------------------------------------------------------


def _event_type(event: object) -> str:
    """Return the short type key for a vtx ``Event``.

    Converts CamelCaseClassName to snake_case_key, stripping ``Event`` suffix.

    Examples:
        TextDeltaEvent  ->  ``text_delta``
        TurnEndEvent    ->  ``turn_end``
        InterruptedEvent ->  ``interrupted`` (no trailing underscore)
    """
    name = type(event).__name__.replace("Event", "")
    parts: list[str] = []
    for ch in name:
        if ch.isupper() and parts:
            parts.append("_")
        parts.append(ch.lower())
    result = "".join(parts)
    # Trim trailing underscore from edge cases like "Interrupted_"
    return result.rstrip("_")


def _tool_result_text(result: Any) -> str:
    """Extract the textual summary from a ToolResultMessage."""
    if result is None:
        return ""
    if hasattr(result, "ui_summary") and result.ui_summary:
        return str(result.ui_summary)
    if hasattr(result, "content") and result.content:
        parts = []
        for c in result.content:
            if hasattr(c, "text") and c.text:
                parts.append(c.text)
        return "\n".join(parts)
    return ""


def _collect_text(buffer: list[str]) -> str:
    """Join the text-buffer segments."""
    return "".join(buffer).strip()


def _estimate_context_tokens(event: Any) -> int:
    """Estimate context tokens from a TurnEndEvent."""
    if event is None:
        return 0
    msg = getattr(event, "assistant_message", None)
    if msg is not None and hasattr(msg, "usage") and msg.usage is not None:
        return getattr(msg.usage, "total_tokens", 0) or 0
    return 0


def _estimate_context_window() -> int:
    """Return a default context window estimate."""
    try:
        import vtx.config as vtx_config_module

        return getattr(vtx_config_module.llm, "context_window", None) or 200000  # type: ignore[attr-defined]
    except Exception:
        return 200000
