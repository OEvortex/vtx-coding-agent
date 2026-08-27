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
    from vtx.ai.agent.tools import lookup_default_tool

    tools: list[Any] = []
    for name in AUDIT_TOOLS:
        tool = lookup_default_tool(name)
        if tool is None:
            try:
                from vtx.coding_agent.tools import tools_by_name

                tool = tools_by_name.get(name)
            except Exception:
                pass
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
    from vtx.ai.agent.runtime import create_provider, default_base_url_for_provider
    from vtx.ai.agent.session import Session

    if provider is None:
        return AuditResult(approved=False, summary="", error="no provider for auditor")

    tools = _build_auditor_tools(cwd)
    session = Session.create(
        cwd=cwd,
        provider=model_provider or getattr(provider.config, "provider", None),
        model_id=model,
        thinking_level=thinking_level or "high",
        system_prompt=None,
        tools=None,
    )

    config = dc_replace(
        provider.config,
        model=model,
        thinking_level=thinking_level or provider.config.thinking_level,
        max_tokens=get_max_tokens(model),
        session_id=session.id,
    )
    effective_base_url = base_url or default_base_url_for_provider(model_provider)
    if effective_base_url:
        config = dc_replace(config, base_url=effective_base_url)
    audit_provider = create_provider(_resolve_api_type(model, model_provider), config)

    agent = Agent(
        provider=audit_provider,
        tools=tools,
        session=session,
        cwd=cwd,
        system_prompt=AUDITOR_SYSTEM_PROMPT,
    )

    final_text = ""
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

                    text = "".join(p.text for p in message.content if isinstance(p, TextContent))
                    if text.strip():
                        final_text = text
            elif isinstance(event, InterruptedEvent):
                return AuditResult(approved=False, summary="", error="aborted", turns=turns)
            elif isinstance(event, ErrorEvent):
                error = str(event.error) if event.error else "auditor error"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.exception("completion audit failed")
        return AuditResult(approved=False, summary="", error=str(exc), turns=turns)

    approved = "<approved/>" in final_text and "<disapproved/>" not in final_text
    if not final_text.strip():
        return AuditResult(
            approved=False, summary="", error=error or "auditor produced no verdict", turns=turns
        )
    return AuditResult(approved=approved, summary=final_text.strip(), turns=turns)


def _resolve_api_type(model: str, model_provider: str | None) -> Any:
    from vtx.ai import get_model, resolve_provider_api_type

    info = get_model(model, model_provider)
    if info is not None:
        return info.api
    return resolve_provider_api_type(model_provider)
