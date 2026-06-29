from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config as vtx_config
from .agents import AgentRegistry, LoadedAgent
from .agents.activate import _filter as _agent_tool_filter
from .agents.activate import compose_active_commands
from .config import get_last_selected, set_last_selected
from .context import Context
from .core.compaction import generate_summary
from .core.handoff import generate_handoff_prompt
from .core.types import AssistantMessage, TextContent, UserMessage
from .extensions import EventBus, LoadedExtensions
from .goal import GoalManager
from .llm import (
    ApiType,
    BaseProvider,
    Model,
    ProviderConfig,
    get_max_tokens,
    get_model,
    get_provider_class,
    resolve_provider_api_type,
)
from .llm.base import AuthMode
from .llm.dynamic_models import find_dynamic_model, get_dynamic_provider_headers
from .loop import Agent
from .prompts import build_system_prompt
from .session import CustomMessageEntry, MessageEntry, Session
from .tools import DEFAULT_TOOLS, BaseTool, tools_by_name

log = logging.getLogger("vtx.runtime")


def default_base_url_for_api(api_type: ApiType) -> str | None:
    if api_type == ApiType.OPENAI_COMPLETIONS or api_type == ApiType.OPENAI_SDK:
        return os.environ.get("VTX_BASE_URL", "https://api.openai.com/v1")
    return None


def default_base_url_for_provider(provider: str | None) -> str | None:
    """Return the canonical base URL for a known provider, if any."""
    if not provider:
        return None
    from .llm.dynamic_models import DYNAMIC_PROVIDERS

    config = DYNAMIC_PROVIDERS.get(provider)
    if config is not None:
        return config.base_url
    return None


def create_provider(api_type: ApiType, config: ProviderConfig) -> BaseProvider:
    """Instantiate a provider, attaching any dynamic-provider default headers."""
    merged_headers = dict(config.default_headers or {})
    merged_headers.update(get_dynamic_provider_headers(config.provider or ""))
    final_config = (
        config
        if not merged_headers
        else ProviderConfig(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            thinking_level=config.thinking_level,
            provider=config.provider,
            session_id=config.session_id,
            openai_compat_auth_mode=config.openai_compat_auth_mode,
            anthropic_compat_auth_mode=config.anthropic_compat_auth_mode,
            default_headers=merged_headers,
        )
    )
    return get_provider_class(api_type)(final_config)


@dataclass
class RuntimeInitResult:
    provider_error: str | None = None


@dataclass
class CompactionResult:
    tokens_before: int
    tokens_after: int = 0


@dataclass
class HandoffResult:
    prompt: str
    source_session: Session
    new_session: Session


@dataclass
class TreeNavigationResult:
    editor_text: str | None = None


class ConversationRuntime:
    def __init__(
        self,
        *,
        cwd: str,
        model: str | None = None,
        model_provider: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        thinking_level: str | None = None,
        tools: list[BaseTool],
        openai_compat_auth_mode: AuthMode = "auto",
        anthropic_compat_auth_mode: AuthMode = "auto",
        extensions: EventBus | None = None,
        agent_registry: AgentRegistry | None = None,
        active_agent: LoadedAgent | None = None,
        agent_extensions: list | None = None,
        persist_last_selected: bool = True,
    ) -> None:
        self.cwd = cwd
        self.persist_last_selected = persist_last_selected
        self._background_manager = None  # installed via ensure_background_manager
        self._background_manager_token = None

        # Resolve the initial agent (CLI > env > config > none). The CLI and
        # the launch path may pass an explicit ``active_agent``; otherwise we
        # use the registry's default.
        self.agent_registry = agent_registry or AgentRegistry()
        if active_agent is not None:
            self.agent_registry.set_active(active_agent.definition.name)

        # Use last selected settings if not explicitly provided
        if model is None or model_provider is None or thinking_level is None:
            last_selected = get_last_selected()
            if model is None:
                self.model = last_selected.model_id or vtx_config.llm.default_model or ""
            else:
                self.model = model

            if model_provider is None:
                self.model_provider = last_selected.provider or (
                    vtx_config.llm.default_provider
                    if model is None and not last_selected.model_id
                    else None
                )
            else:
                self.model_provider = model_provider

            if thinking_level is None:
                self.thinking_level = (
                    last_selected.thinking_level or vtx_config.llm.default_thinking_level or "high"
                )
            else:
                self.thinking_level = thinking_level
        else:
            self.model = model
            self.model_provider = model_provider
            self.thinking_level = thinking_level

        self.api_key = api_key
        self.base_url = base_url
        self.tools = tools
        self.openai_compat_auth_mode: AuthMode = openai_compat_auth_mode
        self.anthropic_compat_auth_mode: AuthMode = anthropic_compat_auth_mode
        self.extensions = extensions

        # Per-session extension list (the ones contributed to the active
        # agent, if any). The launch path is responsible for passing the
        # right list — usually ``agent_extensions`` is the list of loaded
        # Extension objects that should participate in event firing.
        self._agent_extensions = list(agent_extensions or [])

        self.provider: BaseProvider | None = None
        self.session: Session | None = None
        self.agent: Agent | None = None
        self.context: Context | None = None

        # The goal manager is owned here (not on the Agent) so slash
        # commands can mutate its state between runs, and so resume can
        # restore it from the session without rebuilding the agent.
        self.goal_manager: GoalManager = GoalManager(
            max_objective_chars=vtx_config.goal.max_objective_chars,
            max_turns_default=vtx_config.goal.max_turns,
        )

    # ---- agent lifecycle ------------------------------------------------

    @property
    def active_agent(self) -> LoadedAgent | None:
        return self.agent_registry.active

    def set_active_agent(self, name: str | None) -> LoadedAgent | None:
        """Switch the active agent. Returns the new active agent (or None)."""
        previous = self.agent_registry.active
        resolved = self.agent_registry.set_active(name)
        if resolved is None and name is not None:
            return None
        # Persist last-selected.
        if self.persist_last_selected:
            set_last_selected(
                self.model,
                self.model_provider,
                self.thinking_level,
                agent=resolved.definition.name if resolved else None,
            )
        # Wire the agent's event handlers into the extensions bus (if any).
        if resolved is not None and self.extensions is not None:
            resolved.wire_handlers(self.extensions)
        # Rebuild the tool/command set and re-render the system prompt.
        self._apply_active_agent_to_runtime()
        # Fire AGENT_ACTIVATED for the first activation, AGENT_CHANGED for
        # subsequent switches. Done in fire-and-forget style so the UI
        # event loop is not blocked.
        if self.extensions is not None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    tasks: list[asyncio.Task] = []
                    if resolved is not None:
                        tasks.append(
                            loop.create_task(
                                self.extensions.emit(
                                    "agent_activated", agent=resolved.definition.name
                                )
                            )
                        )
                    if previous is not resolved:
                        tasks.append(
                            loop.create_task(
                                self.extensions.emit(
                                    "agent_changed",
                                    previous=previous.definition.name if previous else None,
                                    current=resolved.definition.name if resolved else None,
                                )
                            )
                        )
                    # Keep references so the tasks aren't GC'd before they run.
                    self._pending_agent_event_tasks = tasks
            except RuntimeError:
                # No running loop (tests, headless pre-init); skip.
                pass
        return resolved

    def cycle_active_agent(self) -> LoadedAgent | None:
        """Cycle to the next agent (Shift+Tab). Returns the new active one."""
        new = self.agent_registry.cycle()
        if self.persist_last_selected:
            set_last_selected(
                self.model,
                self.model_provider,
                self.thinking_level,
                agent=new.definition.name if new else None,
            )
        if new is not None and self.extensions is not None:
            new.wire_handlers(self.extensions)
        self._apply_active_agent_to_runtime()
        if self.extensions is not None and new is not None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    self._pending_agent_event_tasks = [
                        loop.create_task(
                            self.extensions.emit("agent_activated", agent=new.definition.name)
                        )
                    ]
            except RuntimeError:
                pass
        return new

    def cycle_active_tool_group(self) -> str | None:
        """Cycle to the next tool group for the active agent. Returns the new group."""
        group = self.agent_registry.cycle_tool_group()
        self._apply_active_agent_to_runtime()
        if self.extensions is not None and group is not None:
            with contextlib.suppress(Exception):
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    self._pending_agent_event_tasks = [
                        loop.create_task(
                            self.extensions.emit(
                                "tool_group_changed",
                                agent=(
                                    self.agent_registry.active.definition.name
                                    if self.agent_registry.active
                                    else None
                                ),
                                group=group,
                            )
                        )
                    ]
        return group

    def _apply_active_agent_to_runtime(self) -> None:
        """Recompute the active tool set + system prompt for the new agent."""
        active = self.active_agent
        base_pool: dict[str, BaseTool] = dict(tools_by_name)
        base_names: list[str] = list(DEFAULT_TOOLS)

        # Build the extension-tools list: session-global + per-agent local.
        ext_tools: list[BaseTool] = []
        for ext in self._agent_extensions:
            ext_tools.extend(ext.tools.values())
        if active is not None and self.extensions is not None:
            # ``extensions`` is the EventBus, not LoadedExtensions here.
            # Pull per-agent local tools from the agent itself.
            ext_tools.extend(active.local_tools.values())
        # Also include session extensions' per-agent local tools if the
        # caller passed them through ``agent_extensions``.
        for ext in self._agent_extensions:
            if active is not None:
                bucket = ext.local_tools.get(active.definition.name)
                if bucket:
                    ext_tools.extend(bucket.values())

        # Determine the effective allow list: profile-level tool groups win
        # over ``tools_allow``.
        allow = None
        if active is not None:
            group = active.definition.active_tool_group
            if group and active.definition.tool_groups:
                group_tools = active.definition.tool_groups.get(group)
                if group_tools:
                    allow = list(group_tools)
            if allow is None and active.definition.tools_allow:
                allow = list(active.definition.tools_allow)
        deny = active.definition.tools_deny if active else []
        # The agent's own local tools are exempt from its allow/deny filters:
        # they were explicitly contributed by the agent, not pulled from the
        # base pool. This lets a profile ship local tools while still
        # restricting the built-in set.
        always_keep = set(active.local_tools.keys()) if active else set()

        new_tools = _agent_tool_filter(
            base_names,
            base_pool,
            {t.name: t for t in ext_tools},
            allow,
            deny,
            always_keep=always_keep,
        )
        self.tools = new_tools
        if self.agent is not None:
            self.agent.tools = self.tools

        # Apply the agent's model/provider/thinking overrides.
        if active is not None:
            d = active.definition
            if d.model is not None:
                self.model = d.model
            if d.provider is not None:
                self.model_provider = d.provider
            if d.thinking_level is not None:
                self.thinking_level = d.thinking_level

        # Keep the Task tool's parent context in sync with the active
        # tool/agent/model state so sub-agents dispatched via ``Task``
        # see fresh data. (TUI also installs its own progress_callback
        # in ConversationRuntime; this call only refreshes the static
        # fields and is safe to repeat.)
        self._refresh_dispatcher_context()

        # Recompute the system prompt (if the agent is initialized).
        if self.agent is not None and self.context is not None:
            self._rebuild_system_prompt()

    def _rebuild_system_prompt(self) -> None:
        """Re-render the system prompt against the active agent.

        Called after a tool/model change that should be visible to the
        model on the next turn.
        """
        active = self.active_agent
        extra = active.definition.instructions if active is not None else None
        mode = active.definition.instructions_mode if active is not None else "append"
        # Filter skills to the ones explicitly listed by the active agent, if any.
        agent_skills: list[Any] | None = None
        if active is not None and active.definition.skills and self.context is not None:
            names = set(active.definition.skills)
            agent_skills = [s for s in self.context.skills if s.name in names or s.path in names]
        new_prompt = build_system_prompt(
            self.cwd,
            context=self.context,
            tools=self.tools,
            extra_instructions=extra,
            extra_instructions_mode=mode,
            skills=agent_skills,
        )
        # Agent._system_prompt is intentionally a private attribute we
        # rebuild on activation; the type checker doesn't know that.
        if self.agent is not None:
            self.agent._system_prompt = new_prompt  # type: ignore[attr-defined]

    def _refresh_dispatcher_context(self) -> None:
        """Re-install the dispatcher context used by sub-agent tools.

        Generic vtx infrastructure: any tool that wants to dispatch a
        sub-agent (e.g. the example ``Task`` tool shipped in
        ``examples/extensions/``) reads from this slot. Idempotent —
        keeps the existing ``progress_callback`` if one was installed
        (typically by the TUI). Called on initialize, agent change,
        model change, and thinking-level change.
        """
        from .dispatcher import DispatcherContext, get_context, set_context

        if self.provider is None or self.agent is None:
            return
        existing = get_context()
        set_context(
            DispatcherContext(
                provider=self.provider,
                model=self.model,
                model_provider=self.model_provider,
                base_url=self.base_url,
                thinking_level=self.thinking_level,
                agent_registry=self.agent_registry,
                cwd=self.cwd,
                system_prompt=self.agent._system_prompt,  # type: ignore[attr-defined]
                progress_callback=existing.progress_callback if existing else None,
                background_manager=self._background_manager,
            )
        )

    def ensure_background_manager(self) -> Any:
        """Lazily construct the :class:`BackgroundTaskManager`.

        Idempotent: a second call returns the same instance. Installs
        the manager into the dispatcher context (via the contextvar)
        so the :class:`TaskTool` can schedule background sub-agents.
        """
        if self._background_manager is None:
            from .tools.background import BackgroundTaskManager, set_manager

            self._background_manager = BackgroundTaskManager()
            self._background_manager_token = set_manager(self._background_manager)
            # Refresh the dispatcher context so the new manager is
            # visible to tools dispatched in this process.
            self._refresh_dispatcher_context()
            if self.agent is not None:
                self.agent._background_manager = self._background_manager
        return self._background_manager

    async def close(self) -> None:
        """Tear down the runtime, cancelling any background sub-agents.

        Called by both the TUI's ``on_unmount`` and the headless
        ``finally`` to ensure no background tasks outlive the parent
        session. Restores the previous dispatcher-contextvar value
        so a second runtime in the same process starts clean.
        """
        if self._background_manager is not None:
            try:
                await self._background_manager.close()
            except Exception:
                log.exception("BackgroundTaskManager.close failed")
            self._background_manager = None
            if self.agent is not None:
                self.agent._background_manager = None
        if self._background_manager_token is not None:
            from .tools.background import reset_manager

            try:
                reset_manager(self._background_manager_token)
            except Exception:
                log.exception("Failed to reset background manager contextvar")
            self._background_manager_token = None

    def active_commands(self) -> dict:
        """The current slash-command dict (session + agent-local).

        Used by the TUI to route ``/foo`` invocations.
        """
        from .extensions import ExtensionCommand

        if not hasattr(self, "_cached_extensions") or self._cached_extensions is None:
            # No extension bus; just return agent-local commands.
            return compose_active_commands(base_commands={}, active_agent=self.active_agent)
        ext: LoadedExtensions = self._cached_extensions
        base: dict[str, ExtensionCommand] = ext.all_commands
        return compose_active_commands(base_commands=base, active_agent=self.active_agent)

    def set_loaded_extensions(self, loaded: LoadedExtensions) -> None:
        """Stash the LoadedExtensions for command routing and re-apply."""
        self._cached_extensions = loaded
        self._agent_extensions = list(loaded.extensions)

    def resolve_system_prompt(
        self, session: Session | None = None, context: Context | None = None
    ) -> str:
        active = self.active_agent
        extra = active.definition.instructions if active is not None else None
        mode = active.definition.instructions_mode if active is not None else "append"
        if active is not None:
            return build_system_prompt(
                self.cwd,
                context=context,
                tools=self.tools,
                extra_instructions=extra,
                extra_instructions_mode=mode,
            )
        return (session.system_prompt if session else None) or build_system_prompt(
            self.cwd,
            context=context,
            tools=self.tools,
            extra_instructions=extra,
            extra_instructions_mode=mode,
        )

    def _provider_config(
        self,
        *,
        model: str,
        provider: str | None,
        base_url: str | None,
        thinking_level: str | None = None,
        session_id: str | None = None,
    ) -> ProviderConfig:
        return ProviderConfig(
            api_key=self.api_key,
            base_url=base_url,
            model=model,
            max_tokens=get_max_tokens(model),
            thinking_level=thinking_level or self.thinking_level,
            provider=provider,
            session_id=session_id,
            openai_compat_auth_mode=self.openai_compat_auth_mode,
            anthropic_compat_auth_mode=self.anthropic_compat_auth_mode,
        )

    def _model_api_and_base_url(
        self, model: str, provider: str | None
    ) -> tuple[ApiType, str | None]:
        model_info = get_model(model, provider)
        if model_info:
            return model_info.api, self.base_url or model_info.base_url
        # Fall back to the dynamic catalog (cache-only) so `--model foo --provider kilo`
        # at startup resolves to the right endpoint without a network call.
        dynamic = find_dynamic_model(model, provider)
        if dynamic is not None:
            return dynamic.api, self.base_url or dynamic.base_url
        api_type = resolve_provider_api_type(provider)
        provider_default = default_base_url_for_provider(provider)
        return api_type, self.base_url or provider_default or default_base_url_for_api(api_type)

    def _new_agent(
        self, provider: BaseProvider, session: Session, context: Context | None = None
    ) -> Agent:
        context = context or Context.load(self.cwd)
        return Agent(
            provider=provider,
            tools=self.tools,
            session=session,
            cwd=self.cwd,
            context=context,
            system_prompt=self.resolve_system_prompt(session, context=context),
            extensions=self.extensions,
            goal_manager=self.goal_manager,
        )

    def initialize(
        self, *, resume_session: str | None = None, continue_recent: bool = False
    ) -> RuntimeInitResult:
        session: Session | None = None
        context = Context.load(self.cwd)
        self.context = context
        model = self.model
        model_provider = self.model_provider
        base_url_override = self.base_url
        thinking_level = self.thinking_level

        if resume_session:
            session = Session.continue_by_id(self.cwd, resume_session)
            if session.entries:
                model_info = session.model
                if model_info:
                    model_provider, model, session_base_url = model_info
                    if base_url_override is None and session_base_url:
                        base_url_override = session_base_url
                thinking_level = session.thinking_level
        elif continue_recent:
            session = Session.continue_recent(
                self.cwd,
                provider=model_provider,
                model_id=model,
                thinking_level=thinking_level,
                system_prompt=self.resolve_system_prompt(None, context=context),
            )
            if session.entries:
                model_info = session.model
                if model_info:
                    model_provider, model, session_base_url = model_info
                    if base_url_override is None and session_base_url:
                        base_url_override = session_base_url
                thinking_level = session.thinking_level

        self.base_url = base_url_override
        api_type, effective_base_url = self._model_api_and_base_url(model, model_provider)
        provider_config = self._provider_config(
            model=model,
            provider=model_provider,
            base_url=effective_base_url,
            thinking_level=thinking_level,
            session_id=session.id if session else None,
        )

        provider: BaseProvider | None = None
        provider_error: str | None = None
        try:
            provider = create_provider(api_type, provider_config)
        except ValueError as e:
            provider_error = str(e)

        if provider:
            valid_levels = provider.thinking_levels
            if thinking_level not in valid_levels:
                thinking_level = valid_levels[0] if valid_levels else "high"
                provider.set_thinking_level(thinking_level)

        if not continue_recent and not resume_session:
            selected_model = get_model(model, model_provider) or find_dynamic_model(
                model, model_provider
            )
            model_provider = (
                selected_model.provider
                if selected_model
                else (provider.name if provider else model_provider)
            )
            session = Session.create(
                self.cwd,
                provider=model_provider,
                model_id=model,
                thinking_level=thinking_level,
                system_prompt=self.resolve_system_prompt(None, context=context),
                tools=[t.name for t in self.tools],
            )
            if model_provider:
                session.append_model_change(model_provider, model, effective_base_url)

        self.model = model
        self.model_provider = model_provider
        self.thinking_level = thinking_level
        self.provider = provider
        self.session = session
        self.agent = self._new_agent(provider, session, context) if provider and session else None
        self._sync_provider_session_id()

        # Install the background-task manager before the dispatcher
        # context so the Task tool sees it. The TUI later overwrites
        # ``progress_callback`` with its chat-log forwarder.
        if provider and session:
            self.ensure_background_manager()
        self._refresh_dispatcher_context()

        if self.persist_last_selected:
            set_last_selected(
                self.model,
                self.model_provider,
                self.thinking_level,
                agent=self.active_agent.definition.name if self.active_agent else None,
            )

        # Restore the active goal from the session (if any). A ``pursuing``
        # goal stays pursuing; terminal goals (achieved / budget_limited /
        # unmet) are kept in the manager for visibility but do not auto-run.
        if self.session is not None:
            self.goal_manager.restore_from_session(self.session)

        return RuntimeInitResult(provider_error=provider_error)

    def _sync_provider_session_id(self) -> None:
        if self.provider and self.session:
            self.provider.config.session_id = self.session.id

    def _current_provider_api_type(self) -> ApiType | None:
        if self.provider is None:
            return None
        if (model_info := get_model(self.model, self.model_provider)) is not None:
            return model_info.api
        try:
            return resolve_provider_api_type(self.model_provider)
        except ValueError:
            return ApiType(ApiType.OPENAI_SDK)

    def create_session(self) -> Session:
        selected_model = get_model(self.model, self.model_provider)
        model_provider = (
            selected_model.provider
            if selected_model
            else (self.provider.name if self.provider else self.model_provider or "openai")
        )
        model_base_url = selected_model.base_url if selected_model else None
        if model_base_url is None and self.provider:
            model_base_url = self.provider.config.base_url

        session = Session.create(
            self.cwd,
            provider=model_provider,
            model_id=self.model,
            thinking_level=self.thinking_level,
            system_prompt=self.resolve_system_prompt(),
            tools=[t.name for t in self.tools],
        )
        session.append_model_change(model_provider, self.model, model_base_url)
        return session

    def new_session(self, *, reload_context: bool = False) -> Session:
        session = self.create_session()
        self.session = session
        self.model_provider = session.model[0] if session.model else self.model_provider
        self._sync_provider_session_id()
        if self.agent is not None:
            self.agent.session = session
            if reload_context:
                self.agent.reload_context()
        elif self.provider is not None:
            self.agent = self._new_agent(self.provider, session)
        return session

    def switch_model(self, model: Model) -> None:
        current_api_type = self._current_provider_api_type()
        current_provider = (
            self.provider.config.provider or self.model_provider
            if self.provider
            else self.model_provider
        )
        current_base_url = self.provider.config.base_url if self.provider else None
        base_url_changed = (current_base_url or "").rstrip("/") != (model.base_url or "").rstrip(
            "/"
        )
        provider_changed = current_provider != model.provider
        replacement_provider: BaseProvider | None = None

        if model.api != current_api_type or provider_changed or base_url_changed:
            provider_config = self._provider_config(
                model=model.id,
                provider=model.provider,
                base_url=model.base_url,
                session_id=self.session.id if self.session else None,
            )
            replacement_provider = create_provider(model.api, provider_config)

        if replacement_provider is not None:
            self.provider = replacement_provider
        elif self.provider:
            self.provider.config.model = model.id
            self.provider.config.base_url = model.base_url
            self.provider.config.max_tokens = get_max_tokens(model.id)
            self.provider.config.provider = model.provider

        self.model = model.id
        self.model_provider = model.provider

        if self.session:
            self.session.set_model(model.provider, model.id, model.base_url)
        if self.agent and self.provider:
            self.agent.provider = self.provider

        # The Task tool's parent context needs a fresh snapshot whenever
        # the model changes.
        self._refresh_dispatcher_context()

        if self.persist_last_selected:
            set_last_selected(
                self.model,
                self.model_provider,
                self.thinking_level,
                agent=self.active_agent.definition.name if self.active_agent else None,
            )

    def set_thinking_level(self, level: str) -> None:
        if self.provider is None:
            return
        self.provider.set_thinking_level(level)
        self.thinking_level = level
        if self.session:
            self.session.set_thinking_level(level)

        # Refresh the Task tool's parent context so sub-agents inherit
        # the new thinking level on the next dispatch.
        self._refresh_dispatcher_context()

        if self.persist_last_selected:
            set_last_selected(
                self.model,
                self.model_provider,
                self.thinking_level,
                agent=self.active_agent.definition.name if self.active_agent else None,
            )

    @property
    def model_supports_thinking(self) -> bool:
        """Whether the currently selected model advertises reasoning/thinking
        support. Models without it should not show a multi-level picker; the
        only meaningful level is ``"none"`` (i.e. just don't think).
        """
        info = get_model(self.model, self.model_provider)
        if info is None:
            # Unknown model: assume it might support thinking so the user
            # can still attempt to set a level. Providers that don't
            # accept the param will 400 and the user sees the error.
            return True
        return bool(info.supports_thinking)

    @property
    def effective_thinking_levels(self) -> list[str]:
        """The set of levels the picker/cycle should offer for the current
        model + provider. Non-thinking models collapse to ``["none"]``;
        all other models expose the full OpenAI-style effort enum.
        """
        if self.provider is None:
            return []
        if not self.model_supports_thinking:
            return ["none"]
        return list(self.provider.thinking_levels)

    def load_session(self, session_path: str | Path) -> Session:
        session = Session.load(session_path)
        model = self.model
        model_provider = self.model_provider
        provider = self.provider
        thinking_level = session.thinking_level

        model_info = session.model
        if model_info:
            model_provider, model, session_base_url = model_info
            restored_model = get_model(model, model_provider)
            restored_base_url = session_base_url or (
                restored_model.base_url if restored_model else None
            )

            if restored_model:
                current_api_type = self._current_provider_api_type()
                if provider is None or restored_model.api != current_api_type:
                    provider_config = self._provider_config(
                        model=model,
                        provider=model_provider,
                        base_url=restored_base_url,
                        thinking_level=thinking_level,
                        session_id=session.id,
                    )
                    provider = create_provider(restored_model.api, provider_config)
            elif provider is None:
                api_type = resolve_provider_api_type(model_provider)
                provider_config = self._provider_config(
                    model=model,
                    provider=model_provider,
                    base_url=restored_base_url or default_base_url_for_api(api_type),
                    thinking_level=thinking_level,
                    session_id=session.id,
                )
                provider = create_provider(api_type, provider_config)
        else:
            restored_base_url = None

        if provider:
            valid_levels = provider.thinking_levels
            if valid_levels and thinking_level not in valid_levels:
                thinking_level = valid_levels[0]

        # Commit only after all provider construction/validation above has succeeded.
        self.session = session
        self.model = model
        self.model_provider = model_provider
        self.thinking_level = thinking_level
        self.provider = provider

        if model_info and self.provider:
            self.provider.config.model = model
            if restored_base_url:
                self.provider.config.base_url = restored_base_url
            self.provider.config.max_tokens = get_max_tokens(model)
            self.provider.config.provider = model_provider
            self.provider.config.session_id = session.id

        if self.provider:
            self.provider.set_thinking_level(thinking_level)
            self.agent = self._new_agent(self.provider, session)
        elif self.agent is not None:
            self.agent.session = session

        if self.persist_last_selected:
            set_last_selected(
                self.model,
                self.model_provider,
                self.thinking_level,
                agent=self.active_agent.definition.name if self.active_agent else None,
            )

        return session

    def navigate_tree(self, entry_id: str) -> TreeNavigationResult:
        if self.session is None:
            raise RuntimeError("Agent not initialized")
        entry = self.session.get_entry(entry_id)
        if entry is None:
            raise ValueError(f"Entry not found: {entry_id}")

        editor_text: str | None = None
        if isinstance(entry, MessageEntry) and isinstance(entry.message, UserMessage):
            self.session.move_to(entry.parent_id)
            content = entry.message.content
            if isinstance(content, str):
                editor_text = content
            else:
                editor_text = "".join(
                    part.text for part in content if isinstance(part, TextContent)
                )
        elif isinstance(entry, CustomMessageEntry):
            self.session.move_to(entry.parent_id)
            editor_text = entry.content
        else:
            self.session.move_to(entry_id)

        if self.agent is not None:
            self.agent.session = self.session
        self._sync_provider_session_id()
        return TreeNavigationResult(editor_text=editor_text)

    def prepare_for_run(self) -> Agent | None:
        if self.provider is None or self.session is None:
            return None
        if self.agent is None:
            self.agent = self._new_agent(self.provider, self.session)

        model_info = get_model(self.model, self.model_provider)
        self.agent.provider = self.provider
        self.agent.session = self.session
        self.agent.tools = self.tools
        self.agent.config.context_window = model_info.context_window if model_info else None
        self.agent.config.max_output_tokens = model_info.max_tokens if model_info else None
        return self.agent

    def reload_context(self) -> None:
        if self.agent is not None:
            self.agent.reload_context()
            self.context = self.agent.context
        else:
            self.context = Context.load(self.cwd)

    def latest_assistant_usage_tokens(self) -> int:
        if self.session is None:
            return 0
        for entry in reversed(self.session.active_entries):
            if isinstance(entry, MessageEntry) and isinstance(entry.message, AssistantMessage):
                usage = entry.message.usage
                if usage is None:
                    continue
                return (
                    usage.input_tokens
                    + usage.output_tokens
                    + usage.cache_read_tokens
                    + usage.cache_write_tokens
                )
        return 0

    # ---- goal-mode management -------------------------------------------

    def set_goal(self, objective: str) -> tuple[Any, str | None]:
        """Set a new goal and persist the snapshot to the active session.

        Returns ``(goal, parse_warning_or_None)``. Raises :class:`ValueError`
        when the master ``goal.enabled`` switch is off, when the
        objective is empty / too long, or when the session is not ready.
        """
        if not vtx_config.goal.enabled:
            raise RuntimeError("goals are disabled (set goal.enabled: true in config)")
        if self.session is None:
            raise RuntimeError("Agent not initialized")
        goal, warning = self.goal_manager.set_goal(objective)
        self.session.append_goal_state(goal.to_dict())
        return goal, warning

    def clear_goal(self) -> Any | None:
        prior = self.goal_manager.clear()
        if self.session is not None and prior is not None:
            prior_dict = prior.to_dict()
            prior_dict["status"] = "cleared"
            prior_dict["completed_at"] = (
                prior_dict.get("completed_at") or datetime.now(UTC).isoformat()
            )
            self.session.append_goal_state(prior_dict)
        return prior

    def pause_goal(self) -> Any | None:
        paused = self.goal_manager.pause()
        if self.session is not None and paused is not None:
            self.session.append_goal_state(paused.to_dict())
        return paused

    def resume_goal(self) -> Any | None:
        resumed = self.goal_manager.resume()
        if self.session is not None and resumed is not None:
            self.session.append_goal_state(resumed.to_dict())
        return resumed

    async def compact_now(self) -> CompactionResult:
        if self.provider is None or self.session is None or self.agent is None:
            raise RuntimeError("Agent not initialized")

        tokens_before = self.latest_assistant_usage_tokens()
        summary = await generate_summary(
            self.session.all_messages, self.provider, system_prompt=self.agent.system_prompt
        )
        self.session.append_compaction(
            summary=summary,
            first_kept_entry_id=self.session.leaf_id or "",
            tokens_before=tokens_before,
        )
        tokens_after = self.session.token_totals().context_tokens
        return CompactionResult(tokens_before=tokens_before, tokens_after=tokens_after)

    async def create_handoff(self, query: str) -> HandoffResult:
        if self.provider is None or self.session is None or self.agent is None:
            raise RuntimeError("Agent not initialized")

        source_session = self.session
        prompt = await generate_handoff_prompt(
            source_session.all_messages,
            self.provider,
            system_prompt=self.agent.system_prompt,
            query=query,
        )

        source_session_id = source_session.id
        new_session = self.create_session()
        new_session.append_custom_message(
            "handoff_backlink",
            f"Handoff from {source_session_id[:8]}",
            display=False,
            details={"target_session_id": source_session_id, "query": query},
        )
        source_session.append_custom_message(
            "handoff_forward_link",
            f"Handoff to {new_session.id[:8]}",
            display=False,
            details={"target_session_id": new_session.id, "query": query},
        )

        new_session.ensure_persisted()
        source_session.ensure_persisted()

        self.session = new_session
        self._sync_provider_session_id()
        if self.agent is not None:
            self.agent.session = new_session

        return HandoffResult(prompt=prompt, source_session=source_session, new_session=new_session)
