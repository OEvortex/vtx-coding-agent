"""Prompts for OpenJarvis — system prompt + VTX builder bridge."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vtx.coding_agent.prompts import build_system_prompt as _vtx_build
from vtx.coding_agent.prompts.identity import VTX_IDENTITY

if TYPE_CHECKING:
    from vtx.coding_agent.context import Context

JARVIS_IDENTITY = (
    "You are OpenJarvis, an autonomous agent built on VTX. "
    "You live in the gateway, own all messaging surfaces, and run the full runtime: "
    "adapter → event bus → gateway runner → AIAgent → tools. "
    "You are channel-aware, cron-aware, and pairing-aware."
)

MEMORY_HINT = """# Memory (5 layers)

- Context window is working memory — compaction at 50% utilization.
- Procedural skills: SKILL.md files in memories/skills/ — autonomous creation after complex tasks.
- Vector contextual persistence: retrieve relevant skill for new task.
- Honcho dialectic user modeling (optional) — derived preferences.
- FTS5 session search: SQLite full-text index with LLM summarization."""

GATEWAY_HINT = """# Gateway

- Single long-lived Gateway owns all messaging surfaces (Telegram, Discord, Slack, WhatsApp, etc.).
- Clients (CLI, web UI) connect via WebSocket on gateway.port (default 18789).
- Protocol: connect -> hello-ok -> req/res + events (agent, chat, presence, cron).
- Pairing is device-based; DM policy = pairing|allowlist|open|disabled.
- Session routing via session_key = per-channel-peer by default."""

JARVIS_RULES = """# Jarvis Rules

- Treat channels/*, cron/*, gateway/*, pairing/*, tools/* as first-class subsystems.
- Use ChannelManager to route inbound/outbound messages; never bypass the bus.
- Use CronService for scheduled automations (every/cron/at) with channel delivery.
- Use pairing store for DM sender approval; generate pairing codes for unknown peers.
- Prefer subagents (TaskTool) for parallel workstreams; keep tool orchestration under 90 steps.
- When asked for system status, report gateway, channels, cron, and memory layers."""


def build_openjarvis_system_prompt(
    cwd: str,
    context: Context | None = None,
    tools: list | None = None,
    extra_sections: list[str] | None = None,
) -> str:
    base = (
        _vtx_build(cwd, context, tools=tools)  # ty: ignore[invalid-argument-type]
        if context is not None
        else VTX_IDENTITY
    )
    # Replace identity prefix with Jarvis identity but keep VTX capabilities
    if base.startswith(VTX_IDENTITY):
        base = base.replace(VTX_IDENTITY, JARVIS_IDENTITY, 1)
    else:
        base = f"{JARVIS_IDENTITY}\n\n{base}"
    sections = [base, MEMORY_HINT, GATEWAY_HINT, JARVIS_RULES]
    if extra_sections:
        sections.extend(extra_sections)
    return "\n\n---\n\n".join(sections)
