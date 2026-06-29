"""Agent handler — bridges vtx_claw channels with :class:`vtx.runtime.ConversationRuntime`.

Instead of manually wiring providers and streaming (the old approach), this
module creates a :class:`~vtx.runtime.ConversationRuntime` per gateway
instance and delegates LLM calls, session management, tool execution,
streaming, and event emission to vtx's battle-tested runtime.

This means claw sessions share the same JSONL persistence, model/provider
resolution, tool surface, extension system, and hook infrastructure as the
TUI and headless CLI.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from vtx import config as vtx_config
from vtx.config import get_last_selected
from vtx.core.types import TextContent
from vtx.events import AgentEndEvent, ErrorEvent, TextDeltaEvent, TurnEndEvent
from vtx.extensions import LoadedExtensions
from vtx.llm import get_model
from typing import Any, cast

from vtx.runtime import ConversationRuntime
# Monkeypatch set_last_selected to be a no-op inside claw daemon
import vtx.runtime
cast(Any, vtx.runtime).set_last_selected = lambda *args, **kwargs: None

from vtx.tools import DEFAULT_TOOLS, get_tools_with_extensions

from vtx_claw.concurrency import SessionLock
from vtx_claw.events import InboundEvent

logger = logging.getLogger(__name__)


class AgentHandler:
    """Gateway agent handler backed by :class:`~vtx.runtime.ConversationRuntime`.

    Creates one runtime per gateway process (shared across channels) and
    delegates every user turn to it.  The runtime handles provider
    initialisation, session persistence, tool execution, streaming,
    compaction, goal management, and the extension/hook system — exactly
    the same pipeline the TUI and headless CLI use.
    """

    def __init__(self, config: Any = None) -> None:
        self.config = config
        self._runtime: ConversationRuntime | None = None
        self._loaded_extensions: LoadedExtensions | None = None
        self._locks = SessionLock()
        self._initialised = False

    # ------------------------------------------------------------------
    # Initialisation (lazy — first message triggers it)
    # ------------------------------------------------------------------

    async def ensure_runtime(self) -> ConversationRuntime:
        """Lazily initialise the ConversationRuntime.

        Idempotent — subsequent calls return the existing runtime.
        Uses the same config resolution chain as vtx's headless CLI:
        claw config → vtx config (~/.vtx/config.yml) → env vars → auth modes.
        """
        if self._initialised and self._runtime is not None:
            return self._runtime

        logger.info("Initialising vtx ConversationRuntime for claw gateway")

        # ── Resolve model, provider, API key, base URL ────────────────────
        # Priority: last-selected > claw config > vtx config > defaults.
        last = get_last_selected()
        model = last.model_id
        provider = last.provider
        base_url = None

        claw_llm = getattr(self.config, "llm", None) if self.config else None

        if not model and claw_llm:
            model = getattr(claw_llm, "default_model", None) or getattr(claw_llm, "model", None)
        if not model:
            model = vtx_config.llm.default_model or "gpt-4o"

        if not provider and claw_llm:
            provider = getattr(claw_llm, "provider", None)
        if not provider:
            provider = vtx_config.llm.default_provider or "openai"

        # Resolve API Key
        # Priority: claw config block > get_dynamic_api_key > env var / OpenAI fallback
        api_key = None
        if claw_llm:
            # First check if the resolved provider has an API key in its claw config block
            prov_block = getattr(claw_llm, provider, None) if provider else None
            if isinstance(prov_block, dict) and prov_block.get("api_key"):
                api_key = prov_block["api_key"]
            else:
                # Grab the first non-empty API key from any provider block in claw config.
                for prov_key in (
                    "openai",
                    "anthropic",
                    "deepseek",
                    "gemini",
                    "grok",
                    "kimi",
                    "glm",
                ):
                    p_block = getattr(claw_llm, prov_key, None) or {}
                    if isinstance(p_block, dict) and p_block.get("api_key"):
                        api_key = p_block["api_key"]
                        break

            if provider == "custom":
                custom_block = getattr(claw_llm, "custom", None) or {}
                if isinstance(custom_block, dict):
                    base_url = custom_block.get("base_url") or None
                    if not api_key:
                        api_key = custom_block.get("api_key") or None
                    if not model or model == "gpt-4o":
                        model = custom_block.get("model") or model

        if not api_key:
            # Check vtx's stored credentials or env vars via get_dynamic_api_key
            from vtx.llm.oauth.dynamic import get_dynamic_api_key

            api_key = get_dynamic_api_key(provider)

        if not api_key:
            # Fallback to OpenAI API key from env or similar as before
            api_key = os.environ.get("OPENAI_API_KEY")

        # Resolve auth modes from vtx config (llm.auth.openai_compat / anthropic_compat).
        # These determine how missing API keys are handled:
        #   "required" → fail if no key
        #   "auto"     → allow local endpoints without key, fail for remote
        #   "none"     → skip key check entirely
        openai_auth = vtx_config.llm.auth.openai_compat
        anthropic_auth = vtx_config.llm.auth.anthropic_compat

        # If no API key is available, try "auto" mode so local/base_url-less
        # endpoints work without requiring the user to set a key explicitly.
        if not api_key:
            from vtx.llm.base import is_local_base_url

            effective_base = base_url or vtx_config.llm.default_base_url or ""
            if not effective_base or is_local_base_url(effective_base):
                if openai_auth == "required":
                    logger.info(
                        "No API key found — overriding openai_compat auth to 'auto' "
                        "for local/empty base_url (%r)",
                        effective_base,
                    )
                    openai_auth = "auto"
            else:
                logger.warning(
                    "No API key for %s provider. Set the %s_API_KEY env var, "
                    "add api_key to ~/.vtx/claw.yml, or configure "
                    "llm.auth.openai_compat='none' in ~/.vtx/config.yml for local endpoints.",
                    provider,
                    provider.upper(),
                )

        # ── Model info ────────────────────────────────────────────────────
        model_info = get_model(model, provider)

        # ── Load extensions ───────────────────────────────────────────────
        from vtx.extensions import load_for_runtime

        self._loaded_extensions = load_for_runtime(cwd=os.getcwd(), auto_discover=True)
        for err in self._loaded_extensions.errors:
            logger.warning("Extension load error: %s", err)

        ext_tools = list(self._loaded_extensions.list_extension_tools())
        tools = get_tools_with_extensions(DEFAULT_TOOLS, ext_tools)

        # ── Build runtime ─────────────────────────────────────────────────
        self._runtime = ConversationRuntime(
            cwd=os.getcwd(),
            model=model,
            model_provider=model_info.provider if model_info else provider,
            api_key=api_key,
            base_url=base_url,
            thinking_level="high",
            tools=tools,
            extensions=self._loaded_extensions.bus,
            openai_compat_auth_mode=openai_auth,
            anthropic_compat_auth_mode=anthropic_auth,
        )
        self._runtime.set_loaded_extensions(self._loaded_extensions)

        init = self._runtime.initialize()
        if init.provider_error:
            logger.error("Runtime init error: %s", init.provider_error)
        else:
            logger.info(
                "Runtime ready — model=%s provider=%s",
                self._runtime.model,
                self._runtime.model_provider,
            )

        self._initialised = True
        return self._runtime

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def handle(self, event: InboundEvent) -> str | None:
        """Handle an incoming message (non-streaming, returns the final text)."""
        runtime = await self.ensure_runtime()
        user_id = self._effective_user_id(event)
        key = f"{event.channel}:{user_id}"

        async with self._locks.lock(key):
            session = runtime.session
            if session is None:
                logger.error("No active session in runtime")
                return "Error: runtime not ready."

            # Append the user message to the vtx session.
            from vtx.core.types import UserMessage

            session.append_message(UserMessage(content=event.text))
            session.ensure_persisted()

            # Prepare the agent and run a single turn.
            agent = runtime.prepare_for_run()
            if agent is None:
                return "Error: agent not initialised."

            full_text = ""
            async for evt in agent.run(event.text):
                match evt:
                    case TextDeltaEvent(delta=d):
                        full_text += d
                    case ErrorEvent(error=err):
                        logger.error("Agent error: %s", err)
                        return full_text if full_text else f"Error: {err}"
                    case TurnEndEvent(assistant_message=msg) if msg is not None:
                        text = "".join(
                            p.text for p in msg.content if isinstance(p, TextContent)
                        ).strip()
                        if text:
                            full_text = text
                    case AgentEndEvent():
                        pass
                    case _:
                        pass

            session.ensure_persisted()
            return full_text or "No response."

    async def handle_streaming(self, event: InboundEvent) -> AsyncIterator[dict[str, str]]:
        """Handle an incoming message with streaming.

        Yields dicts with ``type`` ("delta" | "done" | "error") and ``data``.
        """
        runtime = await self.ensure_runtime()
        user_id = self._effective_user_id(event)
        key = f"{event.channel}:{user_id}"

        async with self._locks.lock(key):
            session = runtime.session
            if session is None:
                yield {"type": "error", "data": "Runtime not ready."}
                return

            from vtx.core.types import UserMessage

            session.append_message(UserMessage(content=event.text))
            session.ensure_persisted()

            agent = runtime.prepare_for_run()
            if agent is None:
                yield {"type": "error", "data": "Agent not initialised."}
                return

            full_text = ""
            async for evt in agent.run(event.text):
                match evt:
                    case TextDeltaEvent(delta=d):
                        full_text += d
                        yield {"type": "delta", "data": d}
                    case ErrorEvent(error=err):
                        logger.error("Agent stream error: %s", err)
                        if not full_text:
                            yield {"type": "error", "data": err}
                        break
                    case TurnEndEvent(assistant_message=msg) if msg is not None:
                        text = "".join(
                            p.text for p in msg.content if isinstance(p, TextContent)
                        ).strip()
                        if text:
                            full_text = text
                    case AgentEndEvent():
                        pass
                    case _:
                        pass

            session.ensure_persisted()
            yield {"type": "done", "data": full_text}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _effective_user_id(self, event: InboundEvent) -> str:
        if (
            self.config
            and getattr(self.config, "isolation", None)
            and getattr(self.config.isolation, "per_group", False)
        ):
            return f"grp:{event.session_id}"
        return event.user_id

    def get_system_prompt(self) -> str:
        if self._runtime is not None and self._runtime.agent is not None:
            return self._runtime.agent.system_prompt
        return "VTX Claw gateway agent"

    async def close(self) -> None:
        """Tear down the runtime (cancels background tasks, persists session)."""
        if self._runtime is not None:
            await self._runtime.close()
            self._initialised = False
