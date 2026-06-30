"""MCP Lifecycle Manager for vtx_claw — lazy/eager/keep-alive server management.

Manages server lifecycle modes, idle timeouts, and auto-disconnect behavior.
Adapted from Jarvis's MCPLifecycleManager.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from vtx_claw.tools.mcp.client import MCPClient

if TYPE_CHECKING:
    from vtx_claw.tools.mcp.registry import MCPRegistry

logger = logging.getLogger(__name__)


class MCPLifecycleManager:
    """Manages lazy/eager/keep-alive server lifecycle.

    Lifecycle modes:
    - lazy (default): Server connects only on first tool call, disconnects after idle_timeout.
    - eager: Server connects at initialization but does NOT auto-reconnect on failure.
    - keep-alive: Server connects at initialization, auto-reconnects on failure via health checks.
    """

    def __init__(self, mcp_registry: MCPRegistry) -> None:
        self._registry = mcp_registry
        self._configs: dict[str, Any] = {}
        self._idle_timers: dict[str, asyncio.Task[None]] = {}
        self._health_tasks: dict[str, asyncio.Task[None]] = {}
        self._last_activity: dict[str, float] = {}
        self._shutting_down = False

    def register_config(self, config: Any) -> None:
        """Register a server config for lifecycle management."""
        self._configs[config.name] = config

    async def initialize_server(self, config: Any) -> None:
        """Initialize a server based on its lifecycle mode.

        - lazy: don't connect yet; just register metadata from cache
        - eager: connect immediately
        - keep-alive: connect immediately + start health check loop
        """
        self.register_config(config)

        if config.lifecycle == "lazy":
            logger.info(
                "MCP server '%s' configured as lazy — will connect on first use", config.name
            )
            return

        # eager or keep-alive: connect now
        try:
            await self._ensure_connected(config.name)
            logger.info("MCP server '%s' connected (%s mode)", config.name, config.lifecycle)

            if config.lifecycle == "keep-alive":
                self._start_health_check(config.name)

        except Exception as e:
            if config.lifecycle == "keep-alive":
                logger.warning(
                    "MCP server '%s' failed to connect (keep-alive will retry): %s", config.name, e
                )
                self._start_health_check(config.name)
            else:
                logger.error("MCP server '%s' failed to connect (eager mode): %s", config.name, e)

    async def ensure_connected(self, server_name: str) -> MCPClient:
        """Ensure a server is connected, connecting lazily if needed."""
        return await self._ensure_connected(server_name)

    async def _ensure_connected(self, server_name: str) -> MCPClient:
        """Internal: connect to a server if not already connected."""
        from vtx_claw.tools.mcp.config import MCPServerConfig

        config = self._configs.get(server_name)
        if not config:
            raise ValueError(f"No config registered for MCP server '{server_name}'")

        client = self._registry.get_client(server_name)
        if client and client.is_connected:
            self._touch_activity(server_name)
            return client

        # Need to connect
        logger.info("Connecting to MCP server '%s' (lifecycle: %s)", server_name, config.lifecycle)
        client = MCPClient(
            config if isinstance(config, MCPServerConfig) else MCPServerConfig(**config)
        )
        await client.connect()

        self._registry._clients[server_name] = client

        self._touch_activity(server_name)
        self._start_idle_timer(server_name)

        return client

    async def on_tool_call(self, server_name: str) -> None:
        """Reset idle timer after a tool call."""
        self._touch_activity(server_name)
        config = self._configs.get(server_name)
        if config and config.lifecycle == "keep-alive" and server_name not in self._health_tasks:
            self._start_health_check(server_name)

    async def disconnect_server(self, server_name: str) -> None:
        """Disconnect a server and clean up its timers."""
        self._cancel_idle_timer(server_name)
        self._cancel_health_check(server_name)

        client = self._registry.get_client(server_name)
        if client:
            try:
                await client.disconnect()
            except Exception as e:
                logger.warning("Error disconnecting MCP server '%s': %s", server_name, e)
            self._registry._clients.pop(server_name, None)

    async def shutdown(self) -> None:
        """Shutdown all servers and cancel all timers."""
        self._shutting_down = True

        for name in list(self._idle_timers.keys()):
            self._cancel_idle_timer(name)
        for name in list(self._health_tasks.keys()):
            self._cancel_health_check(name)
        for name in list(self._configs.keys()):
            client = self._registry.get_client(name)
            if client and client.is_connected:
                with contextlib.suppress(Exception):
                    await client.disconnect()

    def _touch_activity(self, server_name: str) -> None:
        """Record activity for a server, resetting idle timer."""
        self._last_activity[server_name] = time.time()
        config = self._configs.get(server_name)
        if config and config.lifecycle == "lazy" and config.idle_timeout > 0:
            self._start_idle_timer(server_name)

    def _start_idle_timer(self, server_name: str) -> None:
        """Start or restart idle timeout for a lazy server."""
        config = self._configs.get(server_name)
        if not config or config.lifecycle != "lazy" or config.idle_timeout <= 0:
            return
        if self._shutting_down:
            return
        self._cancel_idle_timer(server_name)

        timeout_secs = config.idle_timeout * 60  # Convert minutes to seconds

        async def _idle_timeout() -> None:
            try:
                await asyncio.sleep(timeout_secs)
                if self._shutting_down:
                    return
                last = self._last_activity.get(server_name, 0)
                elapsed = time.time() - last
                if elapsed >= timeout_secs - 1:
                    logger.info("MCP server '%s' idle timeout reached, disconnecting", server_name)
                    await self.disconnect_server(server_name)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning("Idle timer error for '%s': %s", server_name, e)

        self._idle_timers[server_name] = asyncio.create_task(_idle_timeout())

    def _cancel_idle_timer(self, server_name: str) -> None:
        """Cancel idle timer for a server."""
        task = self._idle_timers.pop(server_name, None)
        if task and not task.done():
            task.cancel()

    def _start_health_check(self, server_name: str) -> None:
        """Start periodic health check for a keep-alive server."""
        config = self._configs.get(server_name)
        if not config or config.lifecycle != "keep-alive":
            return
        if self._shutting_down:
            return
        self._cancel_health_check(server_name)

        async def _health_loop() -> None:
            while not self._shutting_down:
                try:
                    await asyncio.sleep(30)
                    if self._shutting_down:
                        return
                    client = self._registry.get_client(server_name)
                    if client and client.is_connected:
                        continue
                    logger.info(
                        "MCP server '%s' disconnected, reconnecting (keep-alive)...", server_name
                    )
                    try:
                        await self._ensure_connected(server_name)
                        logger.info("MCP server '%s' reconnected", server_name)
                    except Exception as e:
                        logger.warning("Failed to reconnect MCP server '%s': %s", server_name, e)
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    logger.warning("Health check error for '%s': %s", server_name, e)

        self._health_tasks[server_name] = asyncio.create_task(_health_loop())

    def _cancel_health_check(self, server_name: str) -> None:
        """Cancel health check for a server."""
        task = self._health_tasks.pop(server_name, None)
        if task and not task.done():
            task.cancel()

    def get_status(self, server_name: str) -> dict[str, Any]:
        """Get lifecycle status for a server."""
        config = self._configs.get(server_name)
        client = self._registry.get_client(server_name)
        last_activity = self._last_activity.get(server_name, 0)
        return {
            "name": server_name,
            "lifecycle": config.lifecycle if config else "unknown",
            "connected": client.is_connected if client else False,
            "idle_timeout": config.idle_timeout if config else 0,
            "last_activity": last_activity,
            "idle_seconds": (time.time() - last_activity if last_activity > 0 else None),
        }
