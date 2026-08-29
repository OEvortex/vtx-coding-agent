"""OpenJarvis runtime — GatewayRunner + AIAgent + VTX harness.

Architecture layer mapping:
  Adapter  -> ChannelManager (per-session_key routing)
  EventBus -> vtx.core.events + openjarvis.bus.queue.MessageBus (optional)
  GatewayRunner -> OpenJarvisRuntime (ThreadPool, agent_cache, session store)
  AIAgent  -> vtx.ai.agent.loop.Agent (VTX-native ReAct)
  Tools    -> vtx.openjarvis.tools + vtx.coding_agent.tools

Gateway mapping:
  Single long-lived Gateway owns all surfaces, WS 18789, pairing device-based,
  multiplexed port (WS control/RPC + HTTP API).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vtx.ai import get_provider_class, resolve_provider_api_type
from vtx.ai.agent.loop import Agent as VtxAgent
from vtx.ai.agent.session import Session

from .config import OpenJarvisConfig
from .context import OpenJarvisContext
from .memory import OpenJarvisMemory
from .prompts import build_openjarvis_system_prompt


def _make_provider(
    model: str | None, provider: str | None, api_key: str | None, base_url: str | None
):
    from vtx.ai import ProviderConfig

    # Resolve via VTX dynamic models when possible; fallback to openai-compatible.
    try:
        from vtx.coding_agent.config import get_config

        cfg = get_config()
        model = model or cfg.llm.default_model
        provider = provider or cfg.llm.default_provider
    except Exception:
        model = model or "gpt-4o-mini"
        provider = provider or "openai"

    api_type = resolve_provider_api_type(provider)
    cfg = ProviderConfig(
        api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
        base_url=base_url or "",
        model=model,
        provider=provider,
    )
    cls = get_provider_class(api_type)
    return cls(cfg)


@dataclass
class RuntimeResult:
    ok: bool
    message: str
    session_id: str | None = None


@dataclass
class _ToolConfigView:
    """Per-tool config sections exposed to the openjarvis tool loader.

    Mirrors the attribute access tools perform on ``ctx.config`` (e.g.
    ``ctx.config.exec.enable``); values are the tools' own pydantic config
    models built from ``OpenJarvisConfig.tools``.
    """

    restrict_to_workspace: bool = False
    exec: Any = None
    file: Any = None
    web: Any = None
    my: Any = None
    image_generation: Any = None
    cli_apps: Any = None


class OpenJarvisRuntime:
    """Single-process runtime — owns channels, cron, pairing, sessions, agent cache."""

    def __init__(self, config: OpenJarvisConfig | None = None, cwd: str | None = None) -> None:
        self.config = config or OpenJarvisConfig.load()
        self.cwd = cwd or self.config.workspace
        self.memory = OpenJarvisMemory()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="openjarvis")
        self._agent_cache: dict[str, VtxAgent] = {}
        self._sessions: dict[str, Session] = {}
        self._channel_manager = None
        self._cron_service = None

    # ---- session / agent caching (agent_cache + session store) ----

    def _session_key_for(self, channel: str, peer: str, scope: str | None = None) -> str:
        scope = scope or self.config.session.dm_scope
        if scope == "main":
            return "main"
        if scope == "per-peer":
            return f"peer:{peer}"
        if scope == "per-channel-peer":
            return f"{channel}:{peer}"
        return f"{channel}:{peer}:{self.config.session.workspace or 'default'}"

    def get_or_create_session(self, session_key: str) -> Session:
        if session_key in self._sessions:
            return self._sessions[session_key]
        # Use VTX Session file store — persist to get_config_dir()/openjarvis/sessions
        from vtx.core.paths import get_config_dir

        sess_dir = Path(get_config_dir()) / "openjarvis" / "sessions"
        sess_dir.mkdir(parents=True, exist_ok=True)
        # VTX Session is JSONL-backed; create via constructor and persist lazily
        sess = Session.create(cwd=self.cwd, system_prompt="")
        # Override id to be stable per session_key for caching
        # Keep original id but map via dict; stable file path via session_key hash
        self._sessions[session_key] = sess
        return sess

    # ---- tool wiring (openjarvis-native tools, not the coding-agent set) ----

    def _tool_config_view(self) -> _ToolConfigView:
        """Build the per-tool config view the openjarvis tool loader expects.

        Sections come from ``OpenJarvisConfig.tools`` (openjarvis.json ``tools``
        key) with sensible defaults, e.g. ``{"exec": {"sandbox": ""}}``.
        """
        from vtx.openjarvis.tools.cli_apps import CliAppsToolConfig
        from vtx.openjarvis.tools.filesystem import FileToolsConfig
        from vtx.openjarvis.tools.image_generation import ImageGenerationToolConfig
        from vtx.openjarvis.tools.self import MyToolConfig
        from vtx.openjarvis.tools.shell import ExecToolConfig
        from vtx.openjarvis.tools.web import WebToolsConfig

        raw = dict(getattr(self.config, "tools", None) or {})
        return _ToolConfigView(
            restrict_to_workspace=bool(raw.get("restrict_to_workspace", False)),
            exec=ExecToolConfig(**(raw.get("exec") or {})),
            file=FileToolsConfig(**(raw.get("file") or {})),
            web=WebToolsConfig(**(raw.get("web") or {})),
            my=MyToolConfig(**(raw.get("my") or {})),
            image_generation=ImageGenerationToolConfig(**(raw.get("image_generation") or {})),
            cli_apps=CliAppsToolConfig(**(raw.get("cli_apps") or {})),
        )

    def build_tools(self) -> list[Any]:
        """Assemble the agent tool list WITHOUT monkey-patching VTX globals.

        Delegates to :func:`get_openjarvis_tools` which already merges the
        curated VTX set (``OPENJARVIS_DEFAULT_TOOLS``) with all
        OpenJarvis-native extras discovered via :class:`ToolLoader`.  We then
        ensure the runtime's ``CronService``/``ToolContext`` wiring takes
        precedence (so ``cron`` is bound to this runtime, not a default
        instance). This keeps ``OpenJarvisRuntime`` and ``register_with_vtx``
        (TUI) in sync — any user-added tool in ``src/vtx/openjarvis/tools/``
        appears in both surfaces.
        """
        from vtx.openjarvis.tools import get_openjarvis_tools

        # get_openjarvis_tools already does the full merge (curated + extras)
        # with a properly wired CronService when we pass it in. For full
        # parity with the TUI path, just delegate.
        tools = get_openjarvis_tools(cron_service=self.cron_service())
        # Ensure channel-aware ToolContext wiring for this runtime (bus/sessions).
        # get_openjarvis_tools used a minimal bus=None context; re-load with the
        # runtime's real bus so MessageTool etc. see the live bus.
        # We do a lightweight re-load only if the bus differs.
        try:
            channel_manager = self.channel_manager()
            real_bus = getattr(channel_manager, "bus", None) if channel_manager else None
            if real_bus is not None:
                from vtx.openjarvis.tools import (
                    OPENJARVIS_DROP_TOOLS,
                    ToolLoader,
                    ToolRegistry,
                    adapt_tool,
                )
                from vtx.openjarvis.tools.context import ToolContext

                tctx = ToolContext(
                    config=self._tool_config_view(),
                    workspace=self.cwd,
                    bus=real_bus,
                    cron_service=self.cron_service(),
                    sessions=self._sessions,
                )
                registry = ToolRegistry()
                try:
                    ToolLoader().load(tctx, registry)
                except Exception:
                    return tools
                # Re-wrap any tools that need the real bus (message, etc.) and
                # append any newly discovered extras not yet in the list.
                present = {t.name for t in tools}
                for name in registry.tool_names:
                    if name in OPENJARVIS_DROP_TOOLS:
                        continue
                    tool = registry.get(name)
                    if tool is None:
                        continue
                    adapted = adapt_tool(tool)
                    if name in present:
                        # Prefer bus-wired instance for message/cron.
                        if name in ("cron", "message"):
                            tools = [adapted if t.name == name else t for t in tools]
                        continue
                    tools.append(adapted)
        except Exception:
            pass
        return tools

    def get_agent(self, session: Session, model: str | None = None) -> VtxAgent:
        key = session.id
        if key in self._agent_cache:
            return self._agent_cache[key]
        ctx = OpenJarvisContext.load(self.cwd, gateway_port=self.config.gateway.port)
        tools = self.build_tools()
        system_prompt = build_openjarvis_system_prompt(self.cwd, ctx, tools=tools)
        provider = _make_provider(
            model or self.config.model, self.config.model_provider, None, None
        )
        agent = VtxAgent(
            provider=provider,
            tools=tools,
            session=session,
            cwd=self.cwd,
            system_prompt=system_prompt,
            context=ctx,
        )
        self._agent_cache[key] = agent
        return agent

    # ---- high-level run (GatewayRunner dispatch) ----

    async def run(
        self,
        query: str,
        channel: str = "cli",
        peer: str = "local",
        images: list | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[Any]:
        """Run one turn — single active run per session_key (single-run invariant)."""
        session_key = self._session_key_for(channel, peer)
        session = self.get_or_create_session(session_key)
        agent = self.get_agent(session)
        async for event in agent.run(query, images=images, cancel_event=cancel_event):
            yield event
        # Remember session for FTS5
        with contextlib.suppress(Exception):
            self.memory.remember_session(session.id, query[:500])

    async def run_sync(self, query: str, channel: str = "cli", peer: str = "local") -> str:
        """Helper for tests/headless — collects final assistant text."""
        chunks: list[str] = []
        async for ev in self.run(query, channel=channel, peer=peer):
            # Collect TextDelta/AssistantMessage
            t = getattr(ev, "delta", None)
            if isinstance(t, str):
                chunks.append(t)
            # TurnEndEvent carries assistant_message
            am = getattr(ev, "assistant_message", None)
            if am is not None:
                try:
                    for c in am.content:
                        if hasattr(c, "text"):
                            chunks.append(c.text)
                except Exception:
                    pass
        return "".join(chunks).strip() if chunks else ""

    # ---- channels / cron / pairing / gateway wiring ----

    def channel_manager(self):
        if self._channel_manager is not None:
            return self._channel_manager
        try:
            from vtx.openjarvis.bus.queue import MessageBus
            from vtx.openjarvis.channels.manager import ChannelManager
            from vtx.openjarvis.config.loader import load_config as _load

            bus = MessageBus()
            cfg = _load()
            self._channel_manager = ChannelManager(cfg, bus)
        except Exception:
            # Fallback stub when openjarvis bus not available
            self._channel_manager = None
        return self._channel_manager

    def cron_service(self):
        if self._cron_service is not None:
            return self._cron_service
        try:
            from vtx.core.paths import get_config_dir
            from vtx.openjarvis.cron.service import CronService

            self._cron_service = CronService(
                store_path=Path(get_config_dir()) / "openjarvis" / "cron.json"
            )
        except Exception:
            self._cron_service = None
        return self._cron_service

    def pairing_store(self):
        try:
            import vtx.openjarvis.pairing as pairing

            return pairing
        except Exception:
            return None

    def gateway_runtime(self):
        try:
            from vtx.openjarvis.gateways.runtime import GatewayRuntime, GatewayStartOptions

            return GatewayRuntime, GatewayStartOptions
        except Exception:
            return None, None

    def cleanup(self) -> None:
        with contextlib.suppress(Exception):
            self._executor.shutdown(wait=False, cancel_futures=True)
