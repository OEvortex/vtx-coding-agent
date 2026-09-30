"""The ``/mcp`` command: show and manage the configured MCP servers.

``/mcp`` with no arguments is a report -- every server, its state, and its tool
count -- because that is what is needed most of the time. The subcommands are
the things worth having a verb for::

    /mcp                 list servers, state, tool counts
    /mcp reload          re-read mcp.json and reconnect
    /mcp reconnect <n>   reconnect one server
    /mcp signin <n>      authorize a remote server (opens a browser)
    /mcp signout <n>     discard that server's stored credentials
    /mcp trust           allow this project's .vtx/mcp.json to run
    /mcp untrust         stop reading it
    /mcp enable <n>      enable a server and save it to its mcp.json
    /mcp disable <n>     disable a server and save it
"""

from __future__ import annotations

import asyncio
import webbrowser

from ..chat import ChatLog
from .base import CommandSupport


class McpCommands(CommandSupport):
    #: True once ``/mcp trust`` has shown what it would run. The grant needs a
    #: second press, so this stands in for a confirmation the caller has to make.
    _mcp_trust_offered: bool = False

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
        if action == "trust":
            self.run_worker(self._mcp_trust(chat, True), exclusive=False)
            return
        if action == "untrust":
            self.run_worker(self._mcp_trust(chat, False), exclusive=False)
            return
        chat.add_info_message(
            "Usage: /mcp [list | reload | reconnect <name> | signin <name> | "
            "signout <name> | trust | untrust | enable <name> | disable <name>]",
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

        # An untrusted project file is reported even though nothing in it is
        # running. Staying silent about it would make the feature undiscoverable
        # and leave a checked-in mcp.json looking like it had no effect.
        self._report_project_config(chat)

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

    def _project_file(self):
        from vtx.mcp.config import project_config_path

        return project_config_path(self._runtime.cwd)

    def _report_project_config(self, chat: ChatLog) -> None:
        """Say whether this project has an ``mcp.json`` and whether it is honored."""
        from vtx.mcp.config import inspect_project_config

        path = self._project_file()
        if not path.is_file() or self._runtime.project_trusted:
            return
        count = len(inspect_project_config(self._runtime.cwd).servers)
        chat.add_info_message(
            f"{path} defines {count} MCP server{'' if count == 1 else 's'}, "
            "but this project is not trusted so none of them are running. "
            "Run /mcp trust to see what it would start.",
            warning=True,
        )

    async def _mcp_trust(self, chat: ChatLog, trust: bool) -> None:
        """Grant or revoke trust for this project's ``mcp.json``.

        The grant path prints every command it would run before doing it. Trust
        here means "run these commands", so a prompt that does not name them is
        not a decision the user can actually make.
        """
        from vtx.mcp.config import inspect_project_config
        from vtx.mcp.trust import ProjectTrustStore

        runtime = self._runtime
        path = self._project_file()
        store = ProjectTrustStore()

        if not trust:
            if not store.untrust(runtime.cwd):
                chat.add_info_message("This project was not trusted.", warning=True)
                return
            tools = await runtime.apply_project_trust(False)
            chat.add_info_message(
                f"No longer reading {path}. {len(tools)} tool"
                f"{'' if len(tools) == 1 else 's'} available."
            )
            self._mcp_list(chat)
            return

        if not path.is_file():
            chat.add_info_message(f"No {path} to trust.", warning=True)
            return

        pending = inspect_project_config(runtime.cwd)
        for message in pending.errors:
            chat.add_info_message(f"config: {message}", error=True)
        if not pending.servers:
            chat.add_info_message(f"{path} defines no usable MCP servers.", warning=True)
            return

        lines = ["This project would start:"]
        for config in pending.servers:
            if config.is_stdio:
                command = " ".join([config.command or "", *config.args]).strip()
                lines.append(f"  {config.name}: {command}")
                if config.cwd:
                    lines.append(f"    in {config.cwd}")
            else:
                lines.append(f"  {config.name}: {config.url}")
        lines.append(f"\nTrust {path} and start these? /mcp trust again to confirm.")
        chat.add_info_message("\n".join(lines), warning=True)

        # Deliberately one-shot: the first /mcp trust shows the commands, the
        # second is the confirmation. A single press that both shows and grants
        # would let anyone who could get a keystroke in run the commands.
        if not self._mcp_trust_offered:
            self._mcp_trust_offered = True
            return

        self._mcp_trust_offered = False
        record = store.trust(runtime.cwd)
        tools = await runtime.apply_project_trust(True)
        chat.add_info_message(
            f"Trusted {record.path} (since {record.trusted_at}). "
            f"{len(tools)} tool{'' if len(tools) == 1 else 's'} available."
        )
        self._mcp_list(chat)

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
