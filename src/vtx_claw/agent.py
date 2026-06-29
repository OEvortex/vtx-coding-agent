from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from vtx_claw.concurrency import SessionLock
from vtx_claw.events import InboundEvent
from vtx_claw.persona import PersonaManager
from vtx_claw.sessions import SessionManager

logger = logging.getLogger(__name__)


class AgentHandler:
    def __init__(self, session_manager: SessionManager, config: Any = None) -> None:
        self.session_manager = session_manager
        self.config = config
        self._provider = None
        self._tools = []
        self._locks = SessionLock()
        self._persona = PersonaManager(
            getattr(config, "persona", None) if config else None
        )

    async def handle(self, event: InboundEvent) -> str | None:
        user_id = self._effective_user_id(event)
        async with self._locks.lock(f"{event.channel}:{user_id}"):
            session = self.session_manager.get_or_create(event.channel, user_id)
            session.add_message("user", event.text)

            history = session.get_history()
            response = await self._call_llm(history)

            if response:
                session.add_message("assistant", response)
                self.session_manager.save(session)

            return response

    async def handle_streaming(
        self, event: InboundEvent
    ) -> AsyncIterator[dict[str, str]]:
        user_id = self._effective_user_id(event)
        async with self._locks.lock(f"{event.channel}:{user_id}"):
            session = self.session_manager.get_or_create(event.channel, user_id)
            session.add_message("user", event.text)

            full_response = ""
            async for chunk in self._stream_llm(session.get_history()):
                full_response += chunk
                yield {"type": "delta", "data": chunk}

            if full_response:
                session.add_message("assistant", full_response)
                self.session_manager.save(session)

            yield {"type": "done", "data": full_response}

    def _effective_user_id(self, event: InboundEvent) -> str:
        if self.config and getattr(self.config, "isolation", None):
            if getattr(self.config.isolation, "per_group", False):
                return f"grp:{event.session_id}"
        return event.user_id

    def get_system_prompt(self) -> str:
        return self._persona.get_system_prompt()

    async def _call_llm(self, history: list[dict[str, str]]) -> str:
        try:
            from vtx.llm import ProviderConfig, get_model, get_provider_class

            model_id = "gpt-4o"
            if self.config and hasattr(self.config, "llm"):
                model_id = getattr(self.config.llm, "default_model", "gpt-4o")

            model = get_model(model_id)
            if not model:
                return "Model not configured. Set llm.default_model in config."

            provider_cls = get_provider_class(model.api)
            provider = provider_cls(
                ProviderConfig(
                    model=model,
                    api_key="",
                )
            )

            from vtx.core.types import UserMessage

            messages = [UserMessage(content=h["content"]) for h in history]
            stream = provider.stream(messages, [], "")
            response = ""
            async for part in stream:
                if hasattr(part, "text") and part.text:
                    response += part.text
            return response or "No response from model."
        except Exception:
            logger.exception("LLM call failed")
            return "Error: failed to get response from LLM."

    async def _stream_llm(
        self, history: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        try:
            from vtx.llm import ProviderConfig, get_model, get_provider_class

            model_id = "gpt-4o"
            if self.config and hasattr(self.config, "llm"):
                model_id = getattr(self.config.llm, "default_model", "gpt-4o")

            model = get_model(model_id)
            if not model:
                yield "Model not configured."
                return

            provider_cls = get_provider_class(model.api)
            provider = provider_cls(
                ProviderConfig(model=model, api_key="")
            )

            from vtx.core.types import UserMessage

            messages = [UserMessage(content=h["content"]) for h in history]
            stream = provider.stream(messages, [], "")
            async for part in stream:
                if hasattr(part, "text") and part.text:
                    yield part.text
        except Exception:
            logger.exception("LLM stream failed")
            yield "Error: stream failed."
