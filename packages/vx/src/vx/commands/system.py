"""``/skills``, ``/tools``, ``/mcp``, ``/reload`` and ``/update``.

These are the commands that talk about the environment rather than the
conversation, ported from ctx-code's catalog, mcp, reload and update mixins.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import sys
import webbrowser
from pathlib import Path
from typing import Any

from ctx.agent.tools import get_tools_with_extensions
from ctx.core.config import config, reload_config

from vx.commands.base import CommandSupport

#: Prefix the extension loader stamps on every module it imports.
EXTENSION_MODULE_PREFIX = "ctx_ext_"

#: Category title when a skill declares none.
UNCATEGORIZED = "general"


def _is_local_skill(skill: object) -> bool:
    """A project skill rather than the global catalog."""
    if getattr(skill, "bundled", False):
        return False
    path = getattr(skill, "path", "") or ""
    if not path:
        return False
    try:
        from ctx.core.paths import get_agents_dir

        global_dir = (get_agents_dir() / "skills").resolve(strict=False)
        return not Path(path).resolve(strict=False).is_relative_to(global_dir)
    except Exception:
        return False


def _skill_sections(skills: list[object]) -> list[tuple[str, list[tuple[str, str]]]]:
    """Skills grouped by category, local ones marked, counts in the titles."""
    groups: dict[str, list[object]] = {}
    for skill in skills:
        category = getattr(skill, "category", None) or UNCATEGORIZED
        groups.setdefault(category, []).append(skill)
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    for category in sorted(groups):
        members = sorted(groups[category], key=lambda s: getattr(s, "name", ""))
        rows = [
            (
                getattr(skill, "name", ""),
                ("local · " if _is_local_skill(skill) else "")
                + " ".join((getattr(skill, "description", "") or "").split())[:70],
            )
            for skill in members
        ]
        sections.append((f"{category} ({len(members)})", rows))
    return sections


def _tool_label(tool: object) -> str:
    server = getattr(tool, "server", None)
    name = getattr(tool, "tool_name", None)
    if server and name:
        return f"{server} > {name}"
    return str(getattr(tool, "name", ""))


def _tool_sections(tools: list[object]) -> list[tuple[str, list[tuple[str, str]]]]:
    """Built-in tools first, then one section per MCP server."""
    builtin: list[str] = []
    servers: dict[str, list[str]] = {}
    for tool in tools:
        server = getattr(tool, "server", None)
        if server:
            servers.setdefault(str(server), []).append(_tool_label(tool))
        else:
            builtin.append(str(getattr(tool, "name", "")))
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    if builtin:
        sections.append((f"built-in ({len(builtin)})", [(name, "") for name in sorted(builtin)]))
    for server in sorted(servers):
        names = sorted(servers[server])
        sections.append((f"{server} ({len(names)})", [(name, "") for name in names]))
    return sections


class SystemCommands(CommandSupport):
    # -- /attach ---------------------------------------------------------

    def _handle_attach_command(self, args: str) -> None:
        """``/attach path.png`` binds an image to the next prompt.

        ctrl+v is the quick road, but a terminal that eats the key or a file
        found by name needs a word, so the same list also shows and clears.
        """
        from vx import attachments as attach

        pending = getattr(self, "_attachments", None)
        if pending is None:
            self._warn("this build cannot carry images")
            return
        argument = args.strip()
        if not argument:
            if not pending:
                self._notice("no images attached · /attach <path> or ctrl+v")
                return
            self._report(
                f"Attached — {len(pending)} image{'s' if len(pending) > 1 else ''}",
                [
                    (
                        "Images",
                        [
                            (item.label, " ".join(filter(None, [item.size_label, item.mime_type])))
                            for item in pending
                        ],
                    )
                ],
            )
            return
        if argument in ("clear", "none", "off"):
            pending.clear()
            self._ok("cleared the attached images")
            return
        found = attach.from_path(argument, self.cwd)
        if found is None:
            self._fail(f"not an image: {argument}")
            return
        pending.append(found)
        chip = f"{found.label} {found.size_label}".strip()
        self._ok(f"attached {chip}")

    # -- /skills and /tools ----------------------------------------------

    def _handle_skills_command(self) -> None:
        rt = self.runtime
        context = getattr(rt, "context", None) if rt else None
        skills = list(getattr(context, "skills", None) or [])
        if not skills:
            self._warn("no skills are loaded in this session")
            return
        for path, message in getattr(context, "skill_warnings", None) or []:
            self._warn(f"warning in {path}: {message}")
        self._report(f"Skills — {len(skills)} loaded", _skill_sections(skills))

    def _handle_tools_command(self) -> None:
        tools = list(getattr(self.runtime, "tools", None) or [])
        if not tools:
            self._warn("no tools are loaded in this session")
            return
        self._report(f"Tools — {len(tools)} available", _tool_sections(tools))

    # -- /mcp ------------------------------------------------------------

    def _handle_mcp_command(self, args: str) -> None:
        parts = args.split()
        action = parts[0].lower() if parts else ""
        target = parts[1] if len(parts) > 1 else ""
        rt = self.runtime
        if rt is None:
            self._warn("MCP is not available yet")
            return

        if action in ("", "list", "ls"):
            self._mcp_list()
            return
        if action == "reload":
            self._spawn(self._mcp_reload())
            return
        if action in ("reconnect", "enable", "disable"):
            if not target:
                self._warn(f"/mcp {action} needs a server name")
                return
            self._spawn(self._mcp_server_action(action, target))
            return
        if action in ("signin", "sign-in", "login", "auth"):
            if not target:
                self._warn("/mcp signin needs a server name")
                return
            self._spawn(self._mcp_sign_in(target))
            return
        if action in ("signout", "sign-out", "logout"):
            if not target:
                self._warn("/mcp signout needs a server name")
                return
            self._spawn(self._mcp_sign_out(target))
            return
        self._warn(
            "usage: /mcp [list | reload | reconnect <name> | signin <name> | "
            "signout <name> | enable <name> | disable <name>]"
        )

    def _mcp_list(self) -> None:
        rt = self.runtime
        if rt is None:
            return
        manager = rt.ensure_mcp_manager()
        statuses = manager.statuses()
        if not statuses:
            where = manager.config_dir / "mcp.json"
            self._notice(
                f"no MCP servers configured. add one to {where}, for example:\n"
                '  {"mcpServers": {"fs": {"command": "npx", "args": '
                '["-y", "@modelcontextprotocol/server-filesystem", "."]}}}',
                topic="mcp",
            )
        else:
            rows = [
                (
                    f"{status.name}" + ("" if status.scope == "global" else f"  [{status.scope}]"),
                    status.describe(),
                )
                for status in statuses
            ]
            self._report("MCP servers", [(f"{len(statuses)} configured", rows)])
        for message in manager.errors:
            self._fail(f"config: {message}")
        for status in statuses:
            if status.authorization_url and status.state == "needs-auth":
                self._notice(
                    f"{status.name} is waiting on this URL:\n{status.authorization_url}",
                    topic="mcp",
                )
        needs_auth = [s.name for s in statuses if s.state == "needs-auth"]
        if needs_auth:
            self._warn(
                "sign-in required for: " + ", ".join(needs_auth) + " — run /mcp signin <name>"
            )

    async def _mcp_reload(self) -> None:
        rt = self.runtime
        if rt is None:
            return
        for message in rt.ensure_mcp_manager().errors:
            self._fail(f"config: {message}")
        try:
            tools = await rt.reload_mcp()
        except Exception as exc:
            self._fail(f"MCP reload failed: {exc}")
            return
        self._ok(
            f"reloaded MCP servers — {len(tools)} tool{'s' if len(tools) != 1 else ''} available",
            topic="mcp",
        )
        self._mcp_list()

    async def _mcp_server_action(self, action: str, name: str) -> None:
        from ctx.mcp.config import update_mcp_server_config

        rt = self.runtime
        if rt is None:
            return
        manager = rt.ensure_mcp_manager()
        connection = manager.get(name)
        if connection is None:
            known = ", ".join(sorted(manager.servers)) or "none"
            self._warn(f"no MCP server named {name!r}. configured: {known}")
            return

        if action in ("enable", "disable"):
            server_config = connection.config
            if not server_config.source:
                self._warn(f"{name!r} was registered in code, so there is no mcp.json to edit")
                return
            try:
                update_mcp_server_config(
                    Path(server_config.source), name, {"enabled": action == "enable"}
                )
            except Exception as exc:
                self._fail(f"could not update {server_config.source}: {exc}")
                return
            server_config.enabled = action == "enable"
            state = "enabled" if action == "enable" else "disabled"
            self._notice(f"{name} {state} in {server_config.source}", topic="mcp")

        try:
            await connection.reconnect()
            rt.sync_mcp_tools(manager.rebuild_tools())
        except Exception as exc:
            self._fail(f"{name}: reconnect failed: {exc}")
            return
        self._ok(f"{name}: {connection.status.describe()}", topic="mcp")

    async def _mcp_sign_in(self, name: str) -> None:
        rt = self.runtime
        if rt is None:
            return
        manager = rt.ensure_mcp_manager()
        connection = manager.get(name)
        if connection is None:
            known = ", ".join(sorted(manager.servers)) or "none"
            self._warn(f"no MCP server named {name!r}. configured: {known}")
            return
        if connection.config.is_stdio:
            self._warn(f"{name!r} runs as a local process, so there is nothing to sign in to")
            return
        self._notice(f"signing in to {name}…", topic="mcp")
        try:
            signed_in = await manager.sign_in(name, open_url=lambda url: self._mcp_open_url(url))
        except Exception as exc:
            self._fail(f"{name}: sign-in failed: {exc}", topic="mcp")
            return
        if not signed_in:
            self._fail(
                f"{name}: sign-in failed. {connection.status.error or 'sign-in did not complete'}",
                topic="mcp",
            )
            return
        rt.sync_mcp_tools(manager.rebuild_tools())
        self._ok(f"{name}: signed in. {connection.status.describe()}", topic="mcp")

    async def _mcp_open_url(self, url: str) -> None:
        self._notice(f"open this URL to authorize:\n{url}", topic="mcp")
        try:
            opened = await asyncio.to_thread(webbrowser.open, url)
        except Exception:
            opened = False
        if not opened:
            self._warn("could not open a browser automatically — use the URL above")

    async def _mcp_sign_out(self, name: str) -> None:
        rt = self.runtime
        if rt is None:
            return
        manager = rt.ensure_mcp_manager()
        connection = manager.get(name)
        if connection is None:
            known = ", ".join(sorted(manager.servers)) or "none"
            self._warn(f"no MCP server named {name!r}. configured: {known}")
            return
        try:
            await connection.oauth_provider().invalidate_credentials("all")
        except Exception as exc:
            self._fail(f"{name}: could not clear credentials: {exc}")
            return
        self._ok(f"{name}: stored credentials cleared", topic="mcp")

    # -- /reload ---------------------------------------------------------

    def _handle_reload_command(self, args: str) -> None:
        if args.strip():
            self._warn("usage: /reload")
            return
        if not self._require_idle("reload"):
            return
        rt = self.runtime
        if rt is None:
            return
        self._spawn(self._do_reload())

    async def _do_reload(self) -> None:
        from ctx.plugins.event_contract import SESSION_SHUTDOWN
        from ctx.plugins.extensions import reload_extensions

        rt = self.runtime
        if rt is None:
            return

        before = self._reload_snapshot()

        # 1. Let the outgoing extensions release what they cached.
        old_bus = getattr(self._loaded, "bus", None)  # type: ignore[attr-defined]
        if old_bus is not None:
            with contextlib.suppress(Exception):
                old_bus.emit_sync(
                    SESSION_SHUTDOWN,
                    cwd=self.cwd,
                    session_id=rt.session.id if rt.session else "",
                    reason="reload",
                )

        # 2. Drop imported extension modules so edited code is re-read.
        importlib.invalidate_caches()
        for name in [n for n in sys.modules if n.startswith(EXTENSION_MODULE_PREFIX)]:
            sys.modules.pop(name, None)

        # 3. Config first: extension and agent discovery read it.
        try:
            reload_config()
        except Exception as exc:
            self._fail(f"config reload failed: {exc}")

        # 4. Custom provider files, before extensions can register their own.
        self._reload_custom_providers()

        # 5. Extensions.
        try:
            loaded = reload_extensions(
                cwd=self.cwd, extra_paths=[*config.extensions], auto_discover=True
            )
        except Exception as exc:
            self._fail(f"extension reload failed: {exc}")
            loaded = None
        if loaded is not None:
            for error in loaded.errors:
                self._fail(f"extension: {error}")
            self._loaded = loaded  # type: ignore[attr-defined]

        # 6. Agents, keeping the active profile if it still resolves.
        from ctx.agent.agents import load_all_agents

        agent_loaded, agent_errors = load_all_agents(
            cwd=self.cwd, configured=list(config.agents.files)
        )
        for error in agent_errors:
            self._fail(f"agent: {error}")
        rt.agent_registry.agents = agent_loaded
        rt.agent_registry.errors = agent_errors
        previous: str | None = before.get("active_agent") or None
        if previous is not None and rt.agent_registry.set_active(previous) is None:
            self._warn(f"active agent {previous!r} no longer resolves; continuing without it")
        active = rt.agent_registry.active

        # 7. Rebuild the tool set from the new inventory.
        ext_tools: list[Any] = []
        if loaded is not None:
            ext_tools.extend(loaded.list_extension_tools())
        if active is not None:
            ext_tools.extend(active.local_tools.values())
            if loaded is not None:
                ext_tools.extend(loaded.local_tools_for(active.definition.name))
        tools = (
            get_tools_with_extensions(None, ext_tools)  # type: ignore[arg-type]
            if ext_tools
            else get_tools_with_extensions()
        )

        # 8. Rebind resources, then tell codemode to re-snapshot its sandbox.
        try:
            rt.rebind_resources(
                tools=tools,
                extensions=loaded.bus if loaded is not None else None,
                loaded_extensions=loaded,
                agent_registry=rt.agent_registry,
                active_agent=active,
            )
        except Exception as exc:
            self._fail(f"rebind failed: {exc}")
        for tool in getattr(rt, "tools", []):
            refresh = getattr(tool, "refresh", None)
            if callable(refresh):
                with contextlib.suppress(Exception):
                    refresh()

        # 9. Context, system prompt and MCP.
        try:
            rt.reload_context()
        except Exception as exc:
            self._fail(f"context reload failed: {exc}")
        try:
            await rt.reload_mcp()
        except Exception as exc:
            self._fail(f"MCP reload failed: {exc}")

        after = self._reload_snapshot()
        self._report_reload(before, after)

    def _reload_snapshot(self) -> dict[str, Any]:
        rt = self.runtime
        context = getattr(rt, "context", None) if rt else None
        loaded = getattr(self, "_loaded", None)
        active = rt.agent_registry.active if rt else None
        return {
            "tools": sorted(str(getattr(t, "name", "")) for t in getattr(rt, "tools", []) or []),
            "skills": len(getattr(context, "skills", None) or []),
            "agents": len(rt.agent_registry.agents) if rt else 0,
            "commands": len(getattr(loaded, "all_commands", {}) or {}),
            "providers": self._provider_count(),
            "theme": config.ui.theme,
            "permissions": config.permissions.mode,
            "active_agent": (active.definition.name if active else None),
        }

    @staticmethod
    def _provider_count() -> int:
        try:
            from ctx.ai.provider_catalog import custom_provider_slugs

            return len(custom_provider_slugs())
        except Exception:
            return 0

    def _reload_custom_providers(self) -> None:
        try:
            from ctx.ai.provider_catalog import reload_custom_providers

            reload_custom_providers()
        except Exception as exc:
            self._fail(f"provider reload failed: {exc}")

    def _report_reload(self, before: dict[str, Any], after: dict[str, Any]) -> None:
        changes: list[str] = []
        before_tools = set(before.get("tools") or [])
        after_tools = set(after.get("tools") or [])
        added = sorted(after_tools - before_tools)
        removed = sorted(before_tools - after_tools)
        if added:
            changes.append(f"+{', '.join(added)}")
        if removed:
            changes.append(f"-{', '.join(removed)}")
        for key, label in (
            ("skills", "skills"),
            ("agents", "agents"),
            ("commands", "commands"),
            ("providers", "providers"),
        ):
            delta = int(after.get(key, 0) or 0) - int(before.get(key, 0) or 0)
            if delta:
                changes.append(f"{label} {delta:+d}")
        rows = [
            ("tools", f"{len(after_tools)} available"),
            ("skills", str(after.get("skills", 0))),
            ("agents", str(after.get("agents", 0))),
            ("commands", str(after.get("commands", 0))),
            ("providers", str(after.get("providers", 0))),
            ("theme", str(after.get("theme", ""))),
            ("permissions", str(after.get("permissions", ""))),
        ]
        self._report(
            "Reloaded config, extensions, agents, tools, skills, hooks, providers, UI",
            [("current", rows)],
            detail="; ".join(changes) if changes else "no visible changes",
        )
        self._refresh_stats(force=True)

    # -- /update ---------------------------------------------------------

    def _handle_update_command(self) -> None:
        if not self._require_idle("update"):
            return
        self._notice("checking for updates…", topic="update")
        self._spawn(self._do_update())

    async def _do_update(self) -> None:
        try:
            from ctx.code.self_update import self_update

            ok, message = await asyncio.to_thread(self_update)
        except Exception as exc:
            self._fail(f"update failed: {exc}", topic="update")
            return
        if not ok:
            self._fail(f"update failed: {message}", topic="update")
            return
        if "already up to date" in message.lower():
            self._ok(message, topic="update")
            return
        self._ok(f"{message} — restart vx to apply the change", topic="update")
