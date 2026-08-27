"""Independent completion audit."""

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

    from vtx.ai import get_max_tokens, get_provider_class, resolve_provider_api_type
    from vtx.ai.agent.loop import Agent
    from vtx.ai.agent.session import Session
    from vtx.ai.dynamic_models import find_dynamic_model
    from vtx.core.types import StopReason, TextContent

    tools = _build_auditor_tools(cwd)
    session = Session.create(
        cwd=cwd,
        provider=model_provider,
        model_id=model,
        thinking_level=thinking_level or "low",
        system_prompt=AUDITOR_SYSTEM_PROMPT,
        tools=tools,
    )

    parent_cfg = getattr(provider, "config", None)
    if parent_cfg is not None:
        auditor_config = dc_replace(
            parent_cfg,
            model=model,
            thinking_level=thinking_level or "low",
            max_tokens=get_max_tokens(model),
            session_id=session.id,
        )
        if base_url:
            auditor_config = dc_replace(auditor_config, base_url=base_url)
        auditor_provider = type(provider)(auditor_config)
    else:
        api_type = resolve_provider_api_type(model_provider)
        dynamic = find_dynamic_model(model, model_provider)
        if dynamic and dynamic.base_url and not base_url:
            base_url = dynamic.base_url

        from vtx.ai import ProviderConfig

        auditor_config = ProviderConfig(
            model=model,
            provider=model_provider or "openai",
            base_url=base_url or "",
            thinking_level=thinking_level or "low",
            session_id=session.id,
        )
        cls = get_provider_class(api_type)
        auditor_provider = cls(auditor_config)

    auditor_agent = Agent(
        provider=auditor_provider,
        tools=tools,
        session=session,
        cwd=cwd,
        system_prompt=AUDITOR_SYSTEM_PROMPT,
    )

    prompt = auditor_prompt(record)
    turns = 0
    final_text = ""

    from vtx.core import (
        AgentEndEvent,
        ApprovalResponse,
        AskUserEvent,
        AskUserResponse,
        ErrorEvent,
        InterruptedEvent,
        ToolApprovalEvent,
        TurnEndEvent,
    )

    try:
        async for event in auditor_agent.run(prompt, cancel_event=cancel_event):
            if isinstance(event, ToolApprovalEvent):
                if event.future is not None and not event.future.done():
                    event.future.set_result(ApprovalResponse.APPROVE)
            elif isinstance(event, AskUserEvent):
                if event.future is not None and not event.future.done():
                    event.future.set_result(AskUserResponse())
            elif isinstance(event, TurnEndEvent):
                turns += 1
                if event.assistant_message is not None:
                    msg = event.assistant_message
                    if msg.stop_reason != StopReason.TOOL_USE:
                        text_parts: list[str] = []
                        for part in msg.content:
                            if isinstance(part, TextContent):
                                text_parts.append(part.text)
                        final_text = "".join(text_parts)
                if turns >= MAX_AUDIT_TURNS:
                    break
            elif isinstance(event, (AgentEndEvent, InterruptedEvent)):
                break
            elif isinstance(event, ErrorEvent):
                return AuditResult(
                    approved=False,
                    summary=f"Auditor failed: {event.error}",
                    error=event.error,
                    turns=turns,
                )
    except asyncio.CancelledError:
        return AuditResult(
            approved=False, summary="Audit cancelled", error="Cancelled", turns=turns
        )
    except Exception as exc:
        log.exception("Completion audit run failed")
        return AuditResult(
            approved=False, summary=f"Audit raised: {exc}", error=str(exc), turns=turns
        )

    return _parse_verdict(final_text, turns)


def _parse_verdict(text: str, turns: int) -> AuditResult:
    clean = text.strip()
    has_approved = "<approved/>" in clean or "<approved />" in clean
    has_disapproved = "<disapproved/>" in clean or "<disapproved />" in clean

    cleaned_summary = (
        clean.replace("<approved/>", "")
        .replace("<approved />", "")
        .replace("<disapproved/>", "")
        .replace("<disapproved />", "")
        .strip()
    )

    if has_approved and not has_disapproved:
        return AuditResult(
            approved=True, summary=cleaned_summary or "Auditor approved completion.", turns=turns
        )
    if has_disapproved and not has_approved:
        return AuditResult(
            approved=False,
            summary=cleaned_summary or "Auditor found unresolved requirements.",
            turns=turns,
        )
    return AuditResult(
        approved=False,
        summary=cleaned_summary or "Auditor reply lacked a conclusive verdict marker.",
        error="ambiguous_verdict",
        turns=turns,
    )


__all__ = ["AUDIT_TOOLS", "MAX_AUDIT_TURNS", "AuditResult", "run_completion_audit"]
