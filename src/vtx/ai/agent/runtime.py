from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vtx.ai import (
    ApiType,
    BaseProvider,
    Model,
    ProviderConfig,
    get_max_tokens,
    get_model,
    get_provider_class,
    resolve_provider_api_type,
)
from vtx.ai.agent.agents import AgentRegistry, LoadedAgent
from vtx.ai.agent.agents.activate import _filter as _agent_tool_filter
from vtx.ai.agent.agents.activate import compose_active_commands
from vtx.ai.agent.context import Context
from vtx.ai.agent.extensions import MODEL_SELECT, THINKING_LEVEL_SELECT, EventBus, LoadedExtensions
from vtx.ai.agent.loop import Agent
from vtx.ai.agent.prompts import build_system_prompt
from vtx.ai.agent.session import CompactionEntry, CustomMessageEntry, MessageEntry, Session
from vtx.ai.agent.tools import BaseTool
from vtx.ai.base import AuthMode
from vtx.ai.config import add_recent_model, get_last_selected, set_last_selected
from vtx.ai.config import config as vtx_config
from vtx.ai.dynamic_models import find_dynamic_model, get_dynamic_provider_headers
from vtx.ai.thinking import clamp_thinking_level
from vtx.core.compaction import SummaryProgress, generate_summary
from vtx.core.handoff import generate_handoff_prompt
from vtx.core.types import AssistantMessage, TextContent, UserMessage

log = logging.getLogger("agent.runtime")


def default_base_url_for_api(api_type: ApiType) -> str | None:
    if api_type == ApiType.OPENAI_SDK:
        return os.environ.get("VTX_BASE_URL", "https://api.openai.com/v1")
    return None


def default_base_url_for_provider(provider: str | None) -> str | None:
    """Return the canonical base URL for a known provider, if any."""
    if not provider:
        return None
    from vtx.ai.dynamic_models import DYNAMIC_PROVIDERS

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
            thinking_level_map=config.thinking_level_map,
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
    summary: str = ""


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
        progress_callback: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.cwd = cwd
        self._progress_callback = progress_callback
        self._background_manager = None  # installed via ensure_background_manager
        self._background_manager_token = None
        self.agent: Agent | None = None

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
        # MCP tools are held apart from `tools` because they arrive after the
        # tool set is built and must survive a reload. See `_sync_mcp_tools`.
        self._mcp_tools: list[BaseTool] = []
        self._mcp_tool_names: set[str] = set()
        self._mcp_manager: Any = None
        self._project_trusted = False
        self.openai_compat_auth_mode: AuthMode = openai_compat_auth_mode
        self.anthropic_compat_auth_mode: AuthMode = anthropic_compat_auth_mode
        self.extensions = extensions

        self._sync_mcp_tools()

        # Per-session extension list (the ones contributed to the active
        # agent, if any). The launch path is responsible for passing the
        # right list — usually ``agent_extensions`` is the list of loaded
        # Extension objects that should participate in event firing.
        self._agent_extensions = list(agent_extensions or [])

        self.provider: BaseProvider | None = None
        self.session: Session | None = None
        self.context: Context | None = None

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
        from vtx.ai.agent.tools import get_all_tools, get_default_tools

        base_pool: dict[str, BaseTool] = get_all_tools()
        base_names: list[str] = get_default_tools()

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
        # The filter rebuilt the set from the built-in and extension pools, so
        # the MCP tools have to be merged back in.
        self._sync_mcp_tools()

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

    def set_progress_callback(self, cb: Callable[[str, dict], None] | None) -> None:
        self._progress_callback = cb
        self._refresh_dispatcher_context()

    def _refresh_dispatcher_context(self) -> None:
        """Re-install the dispatcher context used by sub-agent tools.

        Generic vtx infrastructure: any tool that wants to dispatch a
        sub-agent (e.g. the example ``Task`` tool shipped in
        ``examples/extensions/``) reads from this slot. Idempotent —
        keeps the existing ``progress_callback`` if one was installed
        (typically by the TUI). Called on initialize, agent change,
        model change, and thinking-level change.
        """
        from vtx.ai.agent.dispatcher import DispatcherContext, get_context, set_context

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
                progress_callback=self._progress_callback
                or (existing.progress_callback if existing else None),
                background_manager=self._background_manager,
                session=self.session,
            )
        )

    def ensure_background_manager(self) -> Any:
        """Lazily construct the :class:`BackgroundTaskManager`.

        Idempotent: a second call returns the same instance. Installs
        the manager into the dispatcher context (via the contextvar)
        so the :class:`TaskTool` can schedule background sub-agents.
        """
        if self._background_manager is None:
            from vtx.ai.agent.background import BackgroundTaskManager, set_manager

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
            from vtx.ai.agent.background import reset_manager

            try:
                reset_manager(self._background_manager_token)
            except Exception:
                log.exception("Failed to reset background manager contextvar")
            self._background_manager_token = None
        # A stdio MCP server is a child process holding a pipe. Closing the
        # manager is the only thing that takes it down; leaking one orphans
        # the process for as long as it decides to live.
        if self._mcp_manager is not None:
            try:
                await self._mcp_manager.close()
            except Exception:
                log.exception("MCP manager close failed")
            self._mcp_manager = None
            self._mcp_tools = []
            self._mcp_tool_names = set()

    def active_commands(self) -> dict:
        """The current slash-command dict (session + agent-local).

        Used by the TUI to route ``/foo`` invocations.
        """
        from vtx.ai.agent.extensions import ExtensionCommand

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

    def set_project_trusted(self, trusted: bool) -> None:
        """Whether a project-local ``.vtx/mcp.json`` may be read.

        A project MCP server is a command vtx would execute. Reading one out of
        a repository the user merely opened would mean running code they did
        not ask to run, so this is False until a person grants trust -- see
        :mod:`vtx.mcp.trust`.
        """
        self._project_trusted = trusted

    @property
    def project_trusted(self) -> bool:
        return self._project_trusted

    async def apply_project_trust(self, trusted: bool) -> list[BaseTool]:
        """Set trust and rebuild the servers, so it takes effect now.

        A separate method from :meth:`set_project_trusted` because granting
        trust is useless if the already-built manager keeps its untrusted
        configuration until the next restart. The manager holds its own copy of
        the flag and does the reading, so it has to be told too -- setting only
        the runtime's would reload the same untrusted config.

        The manager is built if it does not exist yet, so the result does not
        depend on whether anything has touched MCP first: a grant made before
        the first ``connect_mcp`` has to work, or it reports success while
        starting nothing.
        """
        self._project_trusted = trusted
        manager = self.ensure_mcp_manager()
        manager.set_project_trusted(trusted)
        return await self.reload_mcp()

    # ---- MCP -------------------------------------------------------------

    @property
    def mcp_manager(self) -> Any:
        return self._mcp_manager

    def ensure_mcp_manager(self) -> Any:
        """Build the MCP manager on first use.

        Lazy, for the same reason extensions are: a session with no
        ``mcp.json`` should pay nothing for the integration.
        """
        if self._mcp_manager is None:
            # Imported here rather than at module scope: vtx.mcp builds on the
            # harness tool contract in vtx.ai.agent.tools, so a top-level
            # import would close the dependency loop.
            from vtx.mcp.manager import McpManager

            self._mcp_manager = McpManager(cwd=self.cwd, project_trusted=self._project_trusted)
            self._mcp_manager.on_tools_changed(self._on_mcp_tools_changed)
        return self._mcp_manager

    async def connect_mcp(self, startup_wait_seconds: float | None = None) -> list[BaseTool]:
        """Connect the configured servers and add their tools.

        Bounded and non-fatal: a slow or broken server reports itself on
        ``/mcp`` rather than holding up or failing the session.
        """
        manager = self.ensure_mcp_manager()
        if startup_wait_seconds is not None:
            manager.startup_wait_seconds = startup_wait_seconds
        for message in manager.errors:
            log.warning("MCP config: %s", message)
        try:
            tools = await manager.connect_all()
        except Exception as exc:
            # A broken integration must not stop the agent from starting.
            log.warning("MCP startup failed: %s", exc)
            return []
        self._mcp_tools = list(tools)
        self._sync_mcp_tools()
        return self._mcp_tools

    async def reload_mcp(self) -> list[BaseTool]:
        """Re-read ``mcp.json``, reconnect, and swap in the new tool set."""
        manager = self.ensure_mcp_manager()
        self._mcp_tools = await manager.reload()
        self._sync_mcp_tools()
        return self._mcp_tools

    def _on_mcp_tools_changed(self, tools: list[BaseTool]) -> None:
        """A server added, removed, or renamed a tool."""
        self.sync_mcp_tools(tools)

    def sync_mcp_tools(self, tools: list[BaseTool]) -> None:
        """Replace the live MCP tool set and re-derive the active tools."""
        self._mcp_tools = list(tools)
        self._sync_mcp_tools()

    def _sync_mcp_tools(self) -> None:
        """Merge the current MCP tools into the active tool set.

        MCP tools are appended *after* an agent profile's allow/deny filter
        rather than put through it. A profile pinning an explicit
        ``tools_allow`` is describing the built-in surface; silently deleting
        every MCP tool because of it would be surprising and hard to diagnose.
        """
        # Drop the previous generation by name, so a removed server's tools
        # actually leave the set instead of accumulating.
        kept = [t for t in self.tools if t.name not in self._mcp_tool_names]
        self.tools = kept + list(self._mcp_tools)
        self._mcp_tool_names = {t.name for t in self._mcp_tools}
        if self.agent is not None:
            self.agent.tools = self.tools
        if self.agent is not None and self.context is not None:
            self._rebuild_system_prompt()

    def _provider_config(
        self,
        *,
        model: str,
        provider: str | None,
        base_url: str | None,
        thinking_level: str | None = None,
        session_id: str | None = None,
    ) -> ProviderConfig:
        info = get_model(model, provider)
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
            thinking_level_map=getattr(info, "thinking_level_map", None)
            if info is not None
            else None,
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
        agent = Agent(
            provider=provider,
            tools=self.tools,
            session=session,
            cwd=self.cwd,
            context=context,
            system_prompt=self.resolve_system_prompt(session, context=context),
            extensions=self.extensions,
        )
        self._apply_model_info(agent)
        return agent

    def _lookup_model_info(self) -> Model | None:
        """Resolve the active model, tolerating stale provider labels.

        Resumed sessions can carry a provider name recorded under legacy
        resolution (e.g. ``openai`` for a custom gateway). Retry without
        the provider filter before giving up so the model's real context
        window is not silently replaced by the harness default.
        """
        info = get_model(self.model, self.model_provider)
        if info is None and self.model_provider:
            info = find_dynamic_model(self.model, None)
            if info is not None:
                log.warning(
                    "Model %r not found under provider %r; matched via catalog as %r",
                    self.model,
                    self.model_provider,
                    info.provider,
                )
        return info

    def _apply_model_info(self, agent: Agent) -> None:
        """Push the active model's limits onto the agent engine config.

        Called on every agent creation/reuse path so overflow compaction
        always sees the model's true context window instead of falling
        back to ``agent.default_context_window``.
        """
        info = self._lookup_model_info()
        if info is None:
            log.warning(
                "No catalog entry for model %r (provider %r); using default "
                "context window %s for compaction",
                self.model,
                self.model_provider,
                agent.config.context_window or "unset",
            )
            return
        agent.config.context_window = info.context_window
        agent.config.max_output_tokens = info.max_tokens

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
        # Self-heal stale provider labels: older sessions could record an
        # engine class name (e.g. "openai") for a custom gateway. If the
        # labeled provider does not know this model but the catalog does,
        # adopt the catalog's canonical provider so lookups, pricing, and
        # context-window resolution all target the right entry.
        if model_provider and get_model(model, model_provider) is None:
            healed = get_model(model) or find_dynamic_model(model, None)
            if healed is not None and healed.provider != model_provider:
                log.warning(
                    "Provider %r has no model %r; using catalog provider %r instead",
                    model_provider,
                    model,
                    healed.provider,
                )
                model_provider = healed.provider
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
            # Validate against what *this model* can express, not the
            # provider's full enum: a saved level (config default, or a
            # session recorded against a different model) can be a tier the
            # model has no wire spelling for, and keeping it would leave the
            # UI showing e.g. "max" while the request silently omits the
            # reasoning parameter and the model falls back to its own default.
            thinking_level = self._clamp_to_supported(
                thinking_level, model=model, model_provider=model_provider, provider=provider
            )
            provider.set_thinking_level(thinking_level)

        if not continue_recent and not resume_session:
            selected_model = get_model(model, model_provider) or find_dynamic_model(
                model, model_provider
            )
            if selected_model:
                model_provider = selected_model.provider
            elif not model_provider and provider is not None:
                # Last resort only: ``provider.name`` is the engine class name
                # ("openai" for every OpenAI-SDK-compatible gateway), NOT a
                # catalog provider. Never let it overwrite a real label.
                log.warning(
                    "Model %r not found in catalog; keeping unlabeled provider "
                    "as %r (engine class name)",
                    model,
                    provider.name,
                )
                model_provider = provider.name
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

        set_last_selected(
            self.model,
            self.model_provider,
            self.thinking_level,
            agent=self.active_agent.definition.name if self.active_agent else None,
        )
        if self.model_provider and self.model:
            add_recent_model(self.model_provider, self.model)

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
        previous_model = self.model
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
            self.provider.config.model = model.effective_id
            self.provider.config.base_url = model.base_url
            self.provider.config.max_tokens = get_max_tokens(model.id)
            self.provider.config.provider = model.provider
            self.provider.config.thinking_level_map = getattr(model, "thinking_level_map", None)

        self.model = model.id
        self.model_provider = model.provider

        # Reconcile the carried-over level with what the new model can express.
        # initialize() does this, but a model switch did not: the level from the
        # previous model survived, so a model that offers only low..max kept
        # showing "none" — a level it cannot send — and the info bar and the
        # provider disagreed about what was in effect.
        if self.provider is not None:
            levels = self.effective_thinking_levels
            if levels and self.thinking_level not in levels:
                clamped = clamp_thinking_level(self.thinking_level, levels)
                self.thinking_level = clamped
                self.provider.set_thinking_level(clamped)
                if self.session:
                    self.session.set_thinking_level(clamped)

        if self.session:
            self.session.set_model(model.provider, model.id, model.base_url)
        if self.agent and self.provider:
            self.agent.provider = self.provider
            # Keep the engine's limits in sync immediately (not just on the
            # next prepare_for_run) so overflow compaction never runs with
            # the previous model's context window.
            self._apply_model_info(self.agent)

        if self.extensions is not None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    self._pending_agent_event_tasks = [
                        loop.create_task(
                            self.extensions.emit(
                                MODEL_SELECT,
                                model=model.id,
                                previous_model=previous_model,
                                source="set",
                            )
                        )
                    ]
            except RuntimeError:
                pass

        # The Task tool's parent context needs a fresh snapshot whenever
        # the model changes.
        self._refresh_dispatcher_context()

        set_last_selected(
            self.model,
            self.model_provider,
            self.thinking_level,
            agent=self.active_agent.definition.name if self.active_agent else None,
        )
        add_recent_model(model.provider, model.id)

    def set_thinking_level(self, level: str) -> None:
        previous_level = self.thinking_level
        if self.provider is None:
            return
        # Clamp into the offered set: a restored session or a model switch can
        # hand us a level this model doesn't support, and the picker/cycle are
        # the only places allowed to change it. Anything else is a no-op rather
        # than an error escaping into the TUI.
        levels = self.effective_thinking_levels
        if levels and level not in levels:
            level = clamp_thinking_level(level, levels)
        self.thinking_level = level
        self.provider.set_thinking_level(level)
        if self.session:
            self.session.set_thinking_level(level)

        if self.extensions is not None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    self._pending_agent_event_tasks = [
                        loop.create_task(
                            self.extensions.emit(
                                THINKING_LEVEL_SELECT, level=level, previous_level=previous_level
                            )
                        )
                    ]
            except RuntimeError:
                pass

        # Refresh the Task tool's parent context so sub-agents inherit
        # the new thinking level on the next dispatch.
        self._refresh_dispatcher_context()

        set_last_selected(
            self.model,
            self.model_provider,
            self.thinking_level,
            agent=self.active_agent.definition.name if self.active_agent else None,
        )

    def set_session_name(self, name: str) -> None:
        if self.session is not None:
            self.session.set_name(name)

    def get_session_name(self) -> str | None:
        if self.session is not None:
            return self.session.name
        return None

    def set_model(self, model: str) -> None:
        self.model = model

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
        """The levels the picker, the ``ctrl+t`` cycle and every other selector
        offer for the current model + provider.

        Delegates to :func:`vtx.ai.thinking.resolve_thinking_levels` so every
        caller sees the same list, derived from the models.dev-verified
        ``thinking_level_map`` and intersected with what the provider can
        express. An unknown model (custom endpoint, not in the catalog) keeps
        the provider's full effort enum so it stays adjustable.
        """
        if self.provider is None:
            return []

        from vtx.ai.thinking import resolve_thinking_levels

        info = get_model(self.model, self.model_provider)
        if info is None:
            return list(self.provider.thinking_levels)
        return resolve_thinking_levels(
            reasoning=info.supports_thinking,
            thinking_level_map=info.thinking_level_map,
            provider_levels=self.provider.thinking_levels,
            style=getattr(self.provider, "reasoning_style", None),
        )

    def _clamp_to_supported(
        self, level: str, *, model: str, model_provider: str | None, provider: BaseProvider
    ) -> str:
        """Clamp a restored/configured level to what this model can express.

        Startup and session restore used to validate only against
        ``provider.thinking_levels`` (the transport's full enum), so a level
        like ``"max"`` survived a switch to a model whose catalog advertises
        no ``max`` tier. The wire layer then dropped the unsupported level and
        the request fell back to the model's own default, leaving the UI
        showing a level that was not in effect. Clamp against the same set the
        picker offers so the displayed level is always the one that is sent.
        """
        from vtx.ai.thinking import resolve_thinking_levels

        info = get_model(model, model_provider)
        if info is None:
            levels = list(provider.thinking_levels)
        else:
            levels = resolve_thinking_levels(
                reasoning=info.supports_thinking,
                thinking_level_map=info.thinking_level_map,
                provider_levels=provider.thinking_levels,
                style=getattr(provider, "reasoning_style", None),
            )
        if not levels or level in levels:
            return level
        return clamp_thinking_level(level, levels)

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
            # Same reason as initialize(): a session's saved level can be a
            # tier the restored model cannot express.
            thinking_level = self._clamp_to_supported(
                thinking_level, model=model, model_provider=model_provider, provider=provider
            )

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
            self.provider.config.thinking_level_map = getattr(
                model_info, "thinking_level_map", None
            )

        if self.provider:
            self.provider.set_thinking_level(thinking_level)
            self.agent = self._new_agent(self.provider, session)
        elif self.agent is not None:
            self.agent.session = session

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

        self.agent.provider = self.provider
        self.agent.session = self.session
        self.agent.tools = self.tools
        self._apply_model_info(self.agent)
        return self.agent

    def reload_context(self) -> None:
        if self.agent is not None:
            self.agent.reload_context()
            self.context = self.agent.context
        else:
            self.context = Context.load(self.cwd)

    def rebind_resources(
        self,
        *,
        tools: list[BaseTool] | None = None,
        extensions: EventBus | None = None,
        loaded_extensions: Any = None,
        agent_registry: AgentRegistry | None = None,
        active_agent: LoadedAgent | None = None,
    ) -> None:
        """Re-point the runtime at freshly loaded resources, keeping the session.

        This is the runtime half of a hot reload. The conversation, provider,
        model, and permission state are deliberately untouched — a reload
        changes what the agent *is made of*, not what it has already said — so
        the whole runtime is never rebuilt, only rebound.

        Order matters twice over. The tool list has to be settled before
        :meth:`reload_context`, because the prompt builder is called with the
        active tool set and a stale list would be baked into the new system
        prompt. And the header prompt is dropped afterwards, because it is
        preferred over a fresh build and would otherwise keep being sent.
        """
        if tools is not None:
            self.tools = tools
            if self.agent is not None:
                self.agent.tools = tools
        if extensions is not None:
            self.extensions = extensions
        if agent_registry is not None:
            self.agent_registry = agent_registry
            self.agent_registry.set_active(
                active_agent.definition.name if active_agent is not None else ""
            )
        elif active_agent is not None:
            self.agent_registry.set_active(active_agent.definition.name)
        if loaded_extensions is not None:
            self.set_loaded_extensions(loaded_extensions)
        # A reload rebuilds the tool set from the built-in and extension pools;
        # MCP tools are in neither, so put them back.
        self._sync_mcp_tools()
        self.reload_context()
        # The header prompt is preferred over a fresh build, so a reload has to
        # drop it or the pre-reload prompt keeps being sent.
        if self.session is not None:
            self.session.set_system_prompt(None)
            self.session.set_header_tools([t.name for t in self.tools])

    def latest_assistant_usage_tokens(self) -> int:
        if self.session is None:
            return 0
        for entry in reversed(self.session.active_entries):
            if isinstance(entry, CompactionEntry):
                return entry.tokens_after or 0
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

    def _estimate_all_messages_tokens(self) -> int:
        """Rough char/4 estimate used when the provider reports no usage."""
        if self.session is None:
            return 0
        total_chars = 0
        for msg in self.session.all_messages:
            content = getattr(msg, "content", None)
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    text = getattr(part, "text", None)
                    if isinstance(text, str):
                        total_chars += len(text)
                    else:
                        thinking = getattr(part, "thinking", None)
                        if isinstance(thinking, str):
                            total_chars += len(thinking)
        return total_chars // 4

    async def compact_now(
        self,
        instructions: str | None = None,
        on_progress: Callable[[SummaryProgress], None] | None = None,
    ) -> CompactionResult:
        if self.provider is None or self.session is None or self.agent is None:
            raise RuntimeError("Agent not initialized")

        tokens_before = self.latest_assistant_usage_tokens()
        if not tokens_before:
            with contextlib.suppress(Exception):
                tokens_before = int(self.session.token_totals().context_tokens)
        if not tokens_before:
            tokens_before = self._estimate_all_messages_tokens()
        summary = await generate_summary(
            self.session.all_messages,
            self.provider,
            system_prompt=self.agent.system_prompt,
            on_delta=on_progress,
            focus_instructions=instructions,
        )

        summary_text = summary

        user_msg = (
            "[Context compacted — conversation history summarized above."
            " Continue working on the task.]"
        )
        tokens_after = (len(summary_text) + len(user_msg)) // 4

        self.session.append_compaction(
            summary=summary,
            first_kept_entry_id=self.session.leaf_id or "",
            tokens_before=tokens_before,
            tokens_after=tokens_after,
        )
        return CompactionResult(
            tokens_before=tokens_before, tokens_after=tokens_after, summary=summary
        )

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
            details={"target_session_id": source_session_id, "query": query, "prompt": prompt},
        )
        source_session.append_custom_message(
            "handoff_forward_link",
            f"Handoff to {new_session.id[:8]}",
            display=False,
            details={"target_session_id": new_session.id, "query": query, "prompt": prompt},
        )

        new_session.ensure_persisted()
        source_session.ensure_persisted()

        self.session = new_session
        self._sync_provider_session_id()
        if self.agent is not None:
            self.agent.session = new_session

        return HandoffResult(prompt=prompt, source_session=source_session, new_session=new_session)
