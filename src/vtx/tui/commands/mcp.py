"""The ``/mcp`` command: show and manage the configured MCP servers.

``/mcp`` with no arguments is a report -- every server, its state, and its tool
count -- because that is what is needed most of the time. The subcommands are
the things worth having a verb for::

    /mcp                 list servers, state, tool counts
    /mcp reload          re-read mcp.json and reconnect
    /mcp reconnect <n>   reconnect one server
    /mcp enable <n>      enable a server and save it to its mcp.json
    /mcp disable <n>     disable a server and save it
"""

from __future__ import annotations

from ..chat import ChatLog
from .base import CommandSupport


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
        chat.add_info_message(
            "Usage: /mcp [list | reload | reconnect <name> | enable <name> | disable <name>]",
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
            return

        lines = []
        for status in statuses:
            scope = "" if status.scope == "global" else f"  [{status.scope}]"
            lines.append(f"  {status.name}{scope}  {status.describe()}")
        chat.add_info_message("MCP servers:\n" + "\n".join(lines))

        for message in manager.errors:
            chat.add_info_message(f"config: {message}", error=True)
        needs_auth = [s.name for s in statuses if s.state == "needs-auth"]
        if needs_auth:
            chat.add_info_message(
                "Sign-in required for: " + ", ".join(needs_auth) + "  (remote servers only)",
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


__all__ = ["McpCommands"]
