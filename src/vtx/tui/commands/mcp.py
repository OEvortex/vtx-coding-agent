"""The ``/mcp`` command: show and manage the configured MCP servers.

``/mcp`` with no arguments is a report -- every server, its state, and its tool
count -- because that is what is needed most of the time. The subcommands are
the things worth having a verb for::

    /mcp                 list servers, state, tool counts
    /mcp reload          re-read mcp.json and reconnect
    /mcp reconnect <n>   reconnect one server
    /mcp signin <n>      authorize a remote server (opens a browser)
    /mcp signout <n>     discard that server's stored credentials
    /mcp enable <n>      enable a server and save it to its mcp.json
    /mcp disable <n>     disable a server and save it
"""

from __future__ import annotations

import asyncio
import webbrowser

from vtx.tui.chat import ChatLog
from vtx.tui.commands.base import CommandSupport


class McpCommands(CommandSupport):
    def _handle_mcp_command(self, args: str) -> None:
        chat = self.query_one("#chat-log", ChatLog)
        parts = args.split()
        action = parts[0].lower() if parts else ""
        target = parts[1] if len(parts) > 1 else ""

        runtime = self._runtime
        if runtime is None:
            chat.add_info_message("MCP is not available yet.", warning=True)
            return

        if action in ("", "list", "ls"):
            self._mcp_list(chat)
            return
        if action == "reload":
            self.run_worker(self._mcp_reload(chat), exclusive=False)
            return
        if action in ("reconnect", "enable", "disable"):
            if not target:
                chat.add_info_message(f"/mcp {action} needs a server name.", warning=True)
                return
            self.run_worker(self._mcp_server_action(chat, action, target), exclusive=False)
            return
        if action in ("signin", "sign-in", "login", "auth"):
            if not target:
                chat.add_info_message("/mcp signin needs a server name.", warning=True)
                return
            self.run_worker(self._mcp_sign_in(chat, target), exclusive=False)
            return
        if action in ("signout", "sign-out", "logout"):
            if not target:
                chat.add_info_message("/mcp signout needs a server name.", warning=True)
                return
            self.run_worker(self._mcp_sign_out(chat, target), exclusive=False)
            return
        chat.add_info_message(
            "Usage: /mcp [list | reload | reconnect <name> | signin <name> | "
            "signout <name> | enable <name> | disable <name>]",
            warning=True,
        )

    # ---- report -----------------------------------------------------------

    def _mcp_list(self, chat: ChatLog) -> None:
        manager = self._runtime.ensure_mcp_manager()
        statuses = manager.statuses()
        if not statuses:
            where = manager.config_dir / "mcp.json"
            chat.add_info_message(
                f"No MCP servers configured. Add one to {where}, for example:\n"
                '  {"mcpServers": {"fs": {"command": "npx", "args": '
                '["-y", "@modelcontextprotocol/server-filesystem", "."]}}}'
            )
        else:
            lines = []
            for status in statuses:
                scope = "" if status.scope == "global" else f"  [{status.scope}]"
                lines.append(f"  {status.name}{scope}  {status.describe()}")
            chat.add_info_message("MCP servers:\n" + "\n".join(lines))

        for message in manager.errors:
            chat.add_info_message(f"config: {message}", error=True)
        for status in statuses:
            # A sign-in that reached a browser redirect but was never finished
            # leaves the link behind. Showing it here is what makes a browser-less
            # or headless run recoverable without retyping anything.
            if status.authorization_url and status.state == "needs-auth":
                chat.add_info_message(
                    f"{status.name} is waiting on this URL:\n{status.authorization_url}"
                )
        needs_auth = [s.name for s in statuses if s.state == "needs-auth"]
        if needs_auth:
            chat.add_info_message(
                "Sign-in required for: " + ", ".join(needs_auth) + "  -- run /mcp signin <name>",
                warning=True,
            )

    # ---- actions ----------------------------------------------------------

    async def _mcp_reload(self, chat: ChatLog) -> None:
        for message in self._runtime.ensure_mcp_manager().errors:
            chat.add_info_message(f"config: {message}", error=True)
        tools = await self._runtime.reload_mcp()
        chat.add_info_message(
            f"Reloaded MCP servers. {len(tools)} tool{'s' if len(tools) != 1 else ''} available."
        )
        self._mcp_list(chat)

    async def _mcp_server_action(self, chat: ChatLog, action: str, name: str) -> None:
        from pathlib import Path

        from vtx.mcp.config import update_mcp_server_config

        runtime = self._runtime
        manager = runtime.ensure_mcp_manager()
        connection = manager.get(name)
        if connection is None:
            known = ", ".join(sorted(manager.servers)) or "none"
            chat.add_info_message(
                f"No MCP server named {name!r}. Configured: {known}", warning=True
            )
            return

        if action in ("enable", "disable"):
            config = connection.config
            if not config.source:
                chat.add_info_message(
                    f"{name!r} was registered in code, so there is no mcp.json to edit.",
                    warning=True,
                )
                return
            try:
                update_mcp_server_config(
                    Path(config.source), name, {"enabled": action == "enable"}
                )
            except Exception as exc:
                chat.add_info_message(f"could not update {config.source}: {exc}", error=True)
                return
            connection.config.enabled = action == "enable"
            chat.add_info_message(
                f"{name} {'enabled' if action == 'enable' else 'disabled'} in {config.source}."
            )

        # enable, disable, and reconnect all end the same way: rebuild this one
        # connection, then re-derive the tool set across every server.
        await connection.reconnect()
        runtime.sync_mcp_tools(manager.rebuild_tools())
        chat.add_info_message(f"{name}: {connection.status.describe()}")

    # ---- OAuth ------------------------------------------------------------

    async def _mcp_sign_in(self, chat: ChatLog, name: str) -> None:
        manager = self._runtime.ensure_mcp_manager()
        connection = manager.get(name)
        if connection is None:
            known = ", ".join(sorted(manager.servers)) or "none"
            chat.add_info_message(
                f"No MCP server named {name!r}. Configured: {known}", warning=True
            )
            return
        if connection.config.is_stdio:
            chat.add_info_message(
                f"{name!r} runs as a local process, so there is nothing to sign in to.",
                warning=True,
            )
            return

        chat.add_info_message(f"Signing in to {name}...")

        async def on_redirect(url: str) -> None:
            # The URL is echoed either way. A browser is a convenience; the
            # terminal is the channel that is certain to be readable, and this
            # is the one moment a user has to act.
            chat.add_info_message(f"Open this URL to authorize:\n{url}")
            try:
                opened = await asyncio.to_thread(webbrowser.open, url)
            except Exception:
                opened = False
            if not opened:
                chat.add_info_message(
                    "Could not open a browser automatically -- use the URL above.", warning=True
                )

        signed_in = await manager.sign_in(name, open_url=on_redirect)
        if not signed_in:
            detail = connection.status.error or "sign-in did not complete"
            chat.add_info_message(f"{name}: sign-in failed. {detail}", error=True)
            return

        self._runtime.sync_mcp_tools(manager.rebuild_tools())
        chat.add_info_message(f"{name}: signed in. {connection.status.describe()}")

    async def _mcp_sign_out(self, chat: ChatLog, name: str) -> None:
        manager = self._runtime.ensure_mcp_manager()
        connection = manager.get(name)
        if connection is None:
            known = ", ".join(sorted(manager.servers)) or "none"
            chat.add_info_message(
                f"No MCP server named {name!r}. Configured: {known}", warning=True
            )
            return
        try:
            await connection.oauth_provider().invalidate_credentials("all")
        except Exception as exc:
            chat.add_info_message(f"{name}: could not clear credentials: {exc}", error=True)
            return
        chat.add_info_message(f"{name}: stored credentials cleared.")


__all__ = ["McpCommands"]
