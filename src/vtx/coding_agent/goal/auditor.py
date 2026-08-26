"""Independent completion audit.

When the executor reports a goal complete, a separate sub-agent re-checks
the objective, task evidence, and verification contract against the real
workspace using read-only tools, then ends with ``<approved/>`` or
``<disapproved/>``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from .prompts import AUDITOR_SYSTEM_PROMPT, auditor_prompt
from .record import GoalRecord

log = logging.getLogger("agent.goal.auditor")

AUDIT_TOOLS = ("read", "grep", "find", "bash")
MAX_AUDIT_TURNS = 30


@dataclass
class AuditResult:
    approved: bool
    summary: str
    error: str | None = None
    turns: int = 0


def _build_auditor_tools(cwd: str) -> list[Any]:
    from vtx.coding_agent.tools import tools_by_name

    tools: list[Any] = []
    for name in AUDIT_TOOLS:
        tool = tools_by_name.get(name)
        if tool is not None:
            tools.append(tool)
    return tools


async def run_completion_audit(
    record: GoalRecord,
    *,
    cwd: str,
    provider: Any,
    model: str,
    model_provider: str | None = None,
    base_url: str | None = None,
    thinking_level: str | None = None,
    cancel_event: asyncio.Event | None = None,
) -> AuditResult:
    """Run the independent auditor sub-agent and parse its verdict."""
    from dataclasses import replace as dc_replace

    from vtx.ai import get_max_tokens
    from vtx.ai.agent.loop import Agent
    from vtx.ai.agent.session import Session
    from vtx.ai.thinking import clamp_thinking_level, get_supported_thinking_levels
    from vtx.coding_agent.runtime import create_provider

    if provider is None:
        return AuditResult(approved=False, summary="", error="no provider for auditor")

    tools = _build_auditor_tools(cwd)
    effective_thinking = (
        thinking_level or getattr(provider.config, "thinking_level", None) or "high"
    )

    # Clamp thinking level if model supports thinking
    info = _lookup_model_info(model, model_provider)
    if info is not None and info.supports_thinking:
        supported = get_supported_thinking_levels(
            reasoning=True, thinking_level_map=getattr(info, "thinking_level_map", None)
        )
        effective_thinking = clamp_thinking_level(effective_thinking, supported)

    session = Session.create(
        cwd=cwd,
        provider=model_provider or getattr(provider.config, "provider", None),
        model_id=model,
        thinking_level=effective_thinking,
        system_prompt=None,
        tools=None,
    )

    # If the parent's provider already matches the active session model, reuse it directly
    if (
        getattr(provider.config, "model", None) == model
        and (
            model_provider is None or getattr(provider.config, "provider", None) == model_provider
        )
        and (base_url is None or getattr(provider.config, "base_url", None) == base_url)
    ):
        audit_provider = provider
    else:
        api_type, effective_base_url = _resolve_api_and_base_url(model, model_provider, base_url)
        config = dc_replace(
            provider.config,
            model=model,
            thinking_level=effective_thinking,
            max_tokens=get_max_tokens(model),
            session_id=session.id,
        )
        if effective_base_url:
            config = dc_replace(config, base_url=effective_base_url)
        audit_provider = create_provider(api_type, config)

    agent = Agent(
        provider=audit_provider,
        tools=tools,
        session=session,
        cwd=cwd,
        system_prompt=AUDITOR_SYSTEM_PROMPT,
    )

    text_parts: list[str] = []
    error: str | None = None
    turns = 0
    try:
        from vtx.core import (
            ApprovalResponse,
            AskUserEvent,
            AskUserResponse,
            ErrorEvent,
            InterruptedEvent,
            TextDeltaEvent,
            TextEndEvent,
            ToolApprovalEvent,
            TurnEndEvent,
        )

        async for event in agent.run(auditor_prompt(record), cancel_event=cancel_event):
            if isinstance(event, TextDeltaEvent | TextEndEvent):
                continue
            if isinstance(event, ToolApprovalEvent):
                if event.future is not None and not event.future.done():
                    event.future.set_result(ApprovalResponse.APPROVE)
            elif isinstance(event, AskUserEvent):
                if event.future is not None and not event.future.done():
                    event.future.set_result(AskUserResponse())
            elif isinstance(event, TurnEndEvent):
                turns += 1
                message = event.assistant_message
                if message is not None:
                    from vtx.core.types import TextContent

                    turn_text = "".join(
                        p.text for p in message.content if isinstance(p, TextContent)
                    )
                    if turn_text.strip():
                        text_parts.append(turn_text.strip())
            elif isinstance(event, InterruptedEvent):
                return AuditResult(approved=False, summary="", error="aborted", turns=turns)
            elif isinstance(event, ErrorEvent):
                error = str(event.error) if event.error else "auditor error"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.exception("completion audit failed")
        return AuditResult(approved=False, summary="", error=str(exc), turns=turns)

    final_text = "\n\n".join(text_parts).strip()
    if not final_text:
        return AuditResult(
            approved=False, summary="", error=error or "auditor produced no verdict", turns=turns
        )

    has_approved = "<approved/>" in final_text or "<approved />" in final_text
    has_disapproved = "<disapproved/>" in final_text or "<disapproved />" in final_text
    approved = has_approved and not has_disapproved

    return AuditResult(approved=approved, summary=final_text, turns=turns)


def _lookup_model_info(model: str, model_provider: str | None) -> Any:
    from vtx.ai import get_model
    from vtx.ai.dynamic_models import find_dynamic_model

    return get_model(model, model_provider) or find_dynamic_model(model, model_provider)


def _resolve_api_and_base_url(
    model: str, provider: str | None, parent_base_url: str | None
) -> tuple[Any, str | None]:
    """Resolve ``(api_type, effective_base_url)`` for a model + provider."""
    from vtx.ai import get_model, resolve_provider_api_type
    from vtx.ai.dynamic_models import find_dynamic_model
    from vtx.coding_agent.runtime import default_base_url_for_api, default_base_url_for_provider

    model_info = get_model(model, provider)
    if model_info:
        return model_info.api, parent_base_url or model_info.base_url
    dynamic = find_dynamic_model(model, provider)
    if dynamic is not None:
        return dynamic.api, parent_base_url or dynamic.base_url
    api_type = resolve_provider_api_type(provider)
    provider_default = default_base_url_for_provider(provider)
    return (api_type, parent_base_url or provider_default or default_base_url_for_api(api_type))
