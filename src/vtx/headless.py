import os
import sys
from collections.abc import AsyncIterator
from typing import TextIO

from vtx import config, get_config
from vtx.config import get_last_selected

from .core.types import StopReason, TextContent
from .events import (
    AgentEndEvent,
    AskUserEvent,
    ErrorEvent,
    Event,
    GoalAchievedEvent,
    GoalBudgetLimitedEvent,
    GoalContinueEvent,
    GoalEvaluatingEvent,
    GoalStartEvent,
    ToolApprovalEvent,
    TurnEndEvent,
)
from .extensions import LoadedExtensions
from .llm.base import AuthMode
from .permissions import ApprovalResponse, AskUserResponse
from .runtime import ConversationRuntime
from .tools import DEFAULT_TOOLS, get_tools_with_extensions

_EXIT_CODES = {StopReason.STOP: 0, StopReason.ERROR: 1, StopReason.LENGTH: 3}


def _exit_code(stop: StopReason) -> int:
    return _EXIT_CODES.get(stop, 1)


def resolve_prompt(prompt_arg: str, *, stdin: TextIO) -> str:
    if prompt_arg == "-":
        return stdin.read().strip()
    return prompt_arg.strip()


async def render_run(
    events: AsyncIterator[Event], *, out: TextIO | None = None, err: TextIO | None = None
) -> StopReason:
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    final_text = ""
    stop = StopReason.ERROR
    async for event in events:
        match event:
            case TurnEndEvent(assistant_message=msg) if msg is not None:
                text = "".join(p.text for p in msg.content if isinstance(p, TextContent)).strip()
                if text:
                    final_text = text
            case GoalStartEvent(objective=objective):
                print(f"goal: {truncate(objective)}", file=err)
            case GoalEvaluatingEvent(turns_evaluated=te):
                print(f"goal: evaluating (turn {te})...", file=err)
            case GoalContinueEvent(reason=reason, turns_evaluated=te):
                print(f"goal: continuing (turn {te}): {truncate(reason)}", file=err)
            case GoalAchievedEvent(reason=reason, turns_evaluated=te):
                print(f"goal: achieved (turn {te}): {truncate(reason)}", file=err)
            case GoalBudgetLimitedEvent(turns_evaluated=te, max_turns=mt):
                print(f"goal: budget-limited after {te} turn(s) (cap {mt})", file=err)
            case AgentEndEvent(stop_reason=stop_reason):
                stop = stop_reason
            case ErrorEvent(error=error):
                print(f"error: {error}", file=err)
            case ToolApprovalEvent(tool_name=tool_name, future=future) if future is not None:
                future.set_result(ApprovalResponse.DENY)
                print(
                    f"error: {tool_name!r} requires approval, denied (non-interactive mode)",
                    file=err,
                )
            case AskUserEvent(question=question, future=future) if future is not None:
                # Headless can't surface a prompt; treat the question as
                # unanswered. The turn runner records a skipped tool
                # result with a hint to the LLM.
                future.set_result(AskUserResponse())
                print(
                    f"error: agent asked a question in non-interactive mode: {question}", file=err
                )
            case _:
                pass
    if stop == StopReason.STOP and final_text:
        print(final_text, file=out)
    return stop


def truncate(text: str, width: int = 100) -> str:
    text = text.strip()
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


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
    goal_objective: str | None = None,
) -> int:
    prompt = resolve_prompt(prompt_arg, stdin=sys.stdin)
    if not prompt:
        print("error: empty prompt", file=sys.stderr)
        return 2

    cfg = get_config()
    previous_permission_mode = cfg.permissions.mode
    # Headless can't show approval prompts; force auto in-memory for this run only.
    cfg.permissions.mode = "auto"

    try:
        last_selected = get_last_selected()
        initial_model = model or last_selected.model_id or config.llm.default_model
        initial_provider = (
            provider
            if provider is not None
            else (
                last_selected.provider
                if last_selected.model_id
                else (config.llm.default_provider if model is None else None)
            )
        )
        base = base_url or config.llm.default_base_url or None
        thinking = last_selected.thinking_level or config.llm.default_thinking_level
        openai_auth = openai_compat_auth_mode or config.llm.auth.openai_compat
        anthropic_auth = anthropic_compat_auth_mode or config.llm.auth.anthropic_compat

        # Load agents first so the active agent's tool surface is applied.
        from .agents import AgentRegistry, load_all_agents
        from .extensions import load_for_runtime

        agent_registry = AgentRegistry()
        if auto_discover_agents or agent_files:
            loaded_agents, agent_errors = load_all_agents(cwd=os.getcwd(), configured=agent_files)
            for err in agent_errors:
                print(f"agent error: {err}", file=sys.stderr)
            agent_registry.agents = loaded_agents
            agent_registry.errors = agent_errors

        # Resolve the initial active agent: CLI > env > last_selected > config > none
        import os as _os

        from .config import get_last_selected as _get_last_selected

        ls = _get_last_selected()
        env_agent = _os.environ.get("VTX_AGENT")
        desired = (
            active_agent_name or env_agent or (ls.agent or None) or (cfg.agents.default or None)
        )
        if desired:
            resolved = agent_registry.set_active(desired)
            if resolved is None:
                print(
                    f"warning: agent {desired!r} not found; running without an active agent",
                    file=sys.stderr,
                )

        tools = get_tools_with_extensions(DEFAULT_TOOLS)

        loaded_extensions = load_for_runtime(cwd=os.getcwd(), auto_discover=True)
        for err in loaded_extensions.errors:
            print(f"extension error: {err}", file=sys.stderr)

        ext_tools = list(loaded_extensions.list_extension_tools())
        if active_agent_name and agent_registry.active is not None:
            ext_tools.extend(agent_registry.active.local_tools.values())
            ext_tools.extend(
                loaded_extensions.local_tools_for(agent_registry.active.definition.name)
            )

        if ext_tools:
            tools = get_tools_with_extensions(DEFAULT_TOOLS, ext_tools)

        runtime = ConversationRuntime(
            cwd=os.getcwd(),
            model=initial_model,
            model_provider=initial_provider,
            api_key=api_key,
            base_url=base,
            thinking_level=thinking,
            tools=tools,
            openai_compat_auth_mode=openai_auth,
            anthropic_compat_auth_mode=anthropic_auth,
            extensions=loaded_extensions.bus,
            agent_registry=agent_registry,
            active_agent=agent_registry.active,
            agent_extensions=list(loaded_extensions.extensions),
        )
        runtime.set_loaded_extensions(loaded_extensions)

        try:
            init = runtime.initialize()
            if init.provider_error:
                print(f"error: {init.provider_error}", file=sys.stderr)
                return 2

            # Set the goal before the run if the caller passed --goal.
            if goal_objective:
                if not config.goal.enabled:
                    print(
                        "warning: --goal ignored (goal.enabled is false in config)",
                        file=sys.stderr,
                    )
                else:
                    try:
                        _goal, warning = runtime.set_goal(goal_objective)
                    except ValueError as exc:
                        print(f"error: {exc}", file=sys.stderr)
                        return 2
                    if warning:
                        print(f"goal: {warning}", file=sys.stderr)

            agent = runtime.prepare_for_run()
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            return 2

        if agent is None:
            print("error: agent initialization failed", file=sys.stderr)
            return 2

        try:
            return _exit_code(await render_run(agent.run(prompt)))
        finally:
            await runtime.close()
    finally:
        cfg.permissions.mode = previous_permission_mode
