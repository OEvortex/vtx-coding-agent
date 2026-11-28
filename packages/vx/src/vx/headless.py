"""``vx -p``: one prompt, one answer, no alternate screen.

Ports the behaviour the TUI already relies on -- extensions, agents, tools,
provider resolution, session persistence -- onto a plain stdout/stderr run.
The transcript stays quiet on purpose: answers go to stdout, diagnostics to
stderr, so ``vx -p ... > file`` is pipeable.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from ctx.core.config import config, get_config, get_last_selected
from ctx.protocol.types import StopReason

import vx.tools
from vx import prompts as vx_prompts

if TYPE_CHECKING:
    from ctx.ai.base import AuthMode
    from ctx.core.events import Event
    from ctx.plugins.extensions import LoadedExtensions

_EXIT_CODES = {StopReason.STOP: 0, StopReason.ERROR: 1, StopReason.LENGTH: 3}
_TOOL_WIDTH = 96


def _exit_code(stop: StopReason) -> int:
    return _EXIT_CODES.get(stop, 1)


def resolve_prompt(prompt_arg: str, *, stdin: TextIO) -> str:
    """``-p`` alone (or ``-``) reads the prompt from stdin."""
    if prompt_arg == "-":
        return stdin.read().strip()
    return prompt_arg.strip()


def _one_line(text: str, width: int = _TOOL_WIDTH) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


async def render_run(
    events: AsyncIterator[Event],
    *,
    out: TextIO | None = None,
    err: TextIO | None = None,
    verbose: bool = False,
) -> StopReason:
    """Print the answer, keep failures on stderr, return the stop reason."""
    from ctx.core.events import (
        AgentEndEvent,
        AskUserEvent,
        ErrorEvent,
        ToolApprovalEvent,
        ToolStartEvent,
        TurnEndEvent,
    )
    from ctx.core.permissions import ApprovalResponse, AskUserResponse
    from ctx.protocol.types import TextContent

    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    final_text = ""
    stop = StopReason.ERROR
    async for event in events:
        if isinstance(event, TurnEndEvent) and event.assistant_message is not None:
            text = "".join(
                p.text for p in event.assistant_message.content if isinstance(p, TextContent)
            ).strip()
            if text:
                final_text = text
        elif isinstance(event, AgentEndEvent):
            stop = event.stop_reason
        elif isinstance(event, ToolStartEvent):
            if verbose:
                print(f"· {_one_line(event.tool_name)}", file=err)
        elif isinstance(event, ErrorEvent):
            print(f"error: {event.error}", file=err)
        elif isinstance(event, ToolApprovalEvent) and event.future is not None:
            # No dialog exists here, so nothing can approve the call.
            event.future.set_result(ApprovalResponse.DENY)
            print(
                f"error: {event.tool_name!r} requires approval, denied (non-interactive mode)",
                file=err,
            )
        elif isinstance(event, AskUserEvent) and event.future is not None:
            event.future.set_result(AskUserResponse())
            asked = "; ".join(q.question for q in event.questions)
            print(f"error: agent asked a question in non-interactive mode: {asked}", file=err)
    if stop == StopReason.STOP and final_text:
        print(final_text, file=out)
    return stop


async def run_headless(
    *,
    prompt_arg: str,
    model: str | None,
    provider: str | None,
    api_key: str | None,
    base_url: str | None,
    openai_compat_auth_mode: AuthMode | None,
    anthropic_compat_auth_mode: AuthMode | None,
    loaded_extensions: LoadedExtensions | None = None,
    active_agent_name: str | None = None,
    agent_files: list[str] | None = None,
    auto_discover_agents: bool = True,
    verbose: bool = False,
) -> int:
    from ctx.agent.agents import AgentRegistry, load_all_agents
    from ctx.agent.runtime import ConversationRuntime
    from ctx.core.session_retention import prune_in_background
    from ctx.plugins.extensions import load_for_runtime

    prompt = resolve_prompt(prompt_arg, stdin=sys.stdin)
    if not prompt:
        print("error: empty prompt", file=sys.stderr)
        return 2

    vx_prompts.register()
    cfg = get_config()

    # Every tool reaches for rg/find; fetching them first beats a first-call stall.
    from ctx.agent.tools_manager import ensure_tools

    await ensure_tools(silent=True)

    # This run writes a new session, so pruning cannot touch it.
    prune_in_background()

    last_selected = get_last_selected()
    initial_model = model or last_selected.model_id or config.llm.default_model
    initial_provider = (
        provider
        if provider is not None
        else (
            last_selected.provider
            if last_selected.provider
            else (config.llm.default_provider if model is None else None)
        )
    )
    thinking = last_selected.thinking_level or config.llm.default_thinking_level

    agent_registry = AgentRegistry()
    if auto_discover_agents or agent_files:
        loaded_agents, agent_errors = load_all_agents(cwd=os.getcwd(), configured=agent_files)
        for err in agent_errors:
            print(f"agent error: {err}", file=sys.stderr)
        agent_registry.agents = loaded_agents
        agent_registry.errors = agent_errors

    desired = (
        active_agent_name or os.environ.get("VX_AGENT") or (last_selected.agent or None) or None
    ) or (cfg.agents.default or None)
    if desired and agent_registry.set_active(desired) is None:
        print(f"warning: agent {desired!r} not found; running without one", file=sys.stderr)

    tools = vx.tools.get_tools()
    loaded = loaded_extensions or load_for_runtime(cwd=os.getcwd())
    for err in loaded.errors:
        print(f"extension error: {err}", file=sys.stderr)
    extra_tools = list(loaded.list_extension_tools())
    if agent_registry.active is not None:
        extra_tools.extend(agent_registry.active.local_tools.values())
        extra_tools.extend(loaded.local_tools_for(agent_registry.active.definition.name))
    if extra_tools:
        tools = [*tools, *extra_tools]

    runtime = ConversationRuntime(
        cwd=os.getcwd(),
        model=initial_model,
        model_provider=initial_provider,
        api_key=api_key,
        base_url=base_url or config.llm.default_base_url or None,
        thinking_level=thinking,
        tools=tools,
        openai_compat_auth_mode=openai_compat_auth_mode or config.llm.auth.openai_compat,
        anthropic_compat_auth_mode=anthropic_compat_auth_mode or config.llm.auth.anthropic_compat,
        extensions=loaded.bus,
        agent_registry=agent_registry,
        active_agent=agent_registry.active,
        agent_extensions=list(loaded.extensions),
    )
    runtime.set_loaded_extensions(loaded)

    from ctx.agent.hooks.bridge import HookBridge

    hook_bridge = HookBridge(
        bus=loaded.bus,
        project_path=Path.cwd() / ".ctx" / "hooks.yml",
        global_path=Path.home() / ".ctx" / "hooks.yml",
    )
    await hook_bridge.load()

    async def _connect_mcp() -> None:
        try:
            found = await runtime.connect_mcp()
        except Exception as exc:
            print(f"warning: MCP startup failed: {exc}", file=sys.stderr)
            return
        if found:
            print(f"mcp: {len(found)} tool(s) connected", file=sys.stderr)

    mcp_task = asyncio.create_task(_connect_mcp())
    # Let the connection start before initialize() blocks the loop.
    await asyncio.sleep(0)

    init = runtime.initialize()
    if init.provider_error:
        print(f"error: {init.provider_error}", file=sys.stderr)
        return 2
    agent = runtime.prepare_for_run()
    if agent is None:
        print("error: agent initialization failed", file=sys.stderr)
        return 2
    try:
        await loaded.bus.emit(
            "session_start",
            cwd=str(Path.cwd()),
            session_id=runtime.session.id if runtime.session else "",
        )
        return _exit_code(await render_run(agent.run(prompt), verbose=verbose))
    finally:
        with contextlib.suppress(BaseException):
            mcp_task.cancel()
            await mcp_task
        with contextlib.suppress(Exception):
            await loaded.bus.emit(
                "session_end",
                cwd=str(Path.cwd()),
                session_id=runtime.session.id if runtime.session else "",
            )
        with contextlib.suppress(Exception):
            from vx.swarm.engine import close_all_engines

            await close_all_engines()
        await hook_bridge.unload()
        await runtime.close()


__all__ = ["render_run", "resolve_prompt", "run_headless"]
