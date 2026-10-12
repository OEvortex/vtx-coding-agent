"""Tests for the /mcp slash command.

The command layer is where the OAuth flow becomes something a person can
actually reach, so the assertions are about what the user is shown and in what
order -- not about the protocol underneath, which the tests in ``tests/mcp``
already cover.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest

from vtx.tui.commands import CommandsMixin

pytestmark = pytest.mark.asyncio


class FakeChat:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.statuses: list[str] = []

    def add_info_message(self, message: str, error: bool = False, warning: bool = False) -> None:
        if error:
            self.errors.append(message)
        elif warning:
            self.warnings.append(message)
        else:
            self.infos.append(message)

    def show_status(self, message: str) -> None:
        self.statuses.append(message)

    @property
    def everything(self) -> str:
        return "\n".join(self.infos + self.warnings + self.errors)


class FakeStatus:
    def __init__(self, name: str, state: str = "connected", **kwargs: Any) -> None:
        self.name = name
        self.state = state
        self.error = kwargs.get("error")
        self.tool_count = kwargs.get("tool_count", 0)
        self.scope = kwargs.get("scope", "global")
        self.authorization_url = kwargs.get("authorization_url")

    def describe(self) -> str:
        if self.state == "connected":
            return f"connected ({self.tool_count} tools)"
        if self.state == "needs-auth":
            return f"needs sign-in: {self.error}" if self.error else "needs sign-in"
        return f"{self.state}: {self.error}" if self.error else self.state


class FakeConnection:
    def __init__(self, name: str, is_stdio: bool = False, **kwargs: Any) -> None:
        self.config = type(
            "C", (), {"name": name, "is_stdio": is_stdio, "source": kwargs.get("source", "")}
        )()
        self.status = FakeStatus(name, **kwargs)
        self.reconnects = 0
        self.sign_ins = 0
        self.sign_in_result = kwargs.get("sign_in_result", True)
        #: False models a refresh token that made the browser unnecessary.
        self.reaches_redirect = kwargs.get("reaches_redirect", True)
        self.tokens = kwargs.get("tokens")
        self.invalidated: list[str] = []

    async def reconnect(self) -> None:
        self.reconnects += 1

    async def sign_in(self, open_url: Any = None) -> bool:
        """Stand in for the real flow, which reaches a browser redirect.

        Calling ``open_url`` is the point: that is where the authorize URL is
        handed over, so a test that never invokes it would pass without ever
        exercising the browser path.
        """
        self.sign_ins += 1
        self.last_open_url = open_url
        if not self.sign_in_result:
            self.status.state = "needs-auth"
            self.status.error = "registration refused"
            return False
        if open_url is not None and self.reaches_redirect:
            result = open_url(f"{self.config.name}-authorize-url")
            if asyncio.iscoroutine(result):
                await result
        self.status.state = "connected"
        return True

    def oauth_provider(self):
        store = self

        class Provider:
            async def invalidate_credentials(self, kind: str) -> None:
                store.invalidated.append(kind)
                store.tokens = None

            async def tokens(self):
                return store.tokens

        return Provider()


class FakeManager:
    def __init__(self, servers: dict[str, FakeConnection], config_dir: Any = None) -> None:
        self.servers = servers
        self.errors: list[str] = []
        self.config_dir = config_dir
        self.rebuilds = 0
        self.signed_in: list[str] = []

    def get(self, name: str):
        return self.servers.get(name)

    def statuses(self):
        return [c.status for c in self.servers.values()]

    def rebuild_tools(self):
        self.rebuilds += 1
        return []

    async def sign_in(self, name: str, open_url: Any = None) -> bool:
        self.signed_in.append(name)
        connection = self.get(name)
        if connection is None:
            return False
        return await connection.sign_in(open_url=open_url)


class FakeRuntime:
    def __init__(self, cwd: str, manager: FakeManager | None = None) -> None:
        self.cwd = cwd
        self._manager = manager or FakeManager({}, config_dir=Path(cwd))
        self.tools: list[Any] = []
        self.reloads = 0

    def ensure_mcp_manager(self) -> FakeManager:
        return self._manager

    def sync_mcp_tools(self, tools: list[Any]) -> None:
        self.tools = tools


class FakeCommands(CommandsMixin):
    def __init__(self, runtime: FakeRuntime) -> None:
        self.chat = FakeChat()
        self._runtime = cast("Any", runtime)
        self.workers: list[Any] = []

    def query_one(self, selector, widget_type):
        if selector == "#chat-log":
            return self.chat
        raise AssertionError(f"Unexpected selector: {selector}")

    def run_worker(self, coro, exclusive: bool = True):
        """Run the worker's coroutine to completion.

        Synchronous to completion so assertions can follow a command, which is
        what a test of a two-press confirmation needs.
        """
        self.workers.append(coro)
        return asyncio.get_event_loop().run_until_complete(coro)


# ---- reporting ------------------------------------------------------------


async def test_no_servers_explains_where_to_put_one(tmp_path):
    runtime = FakeRuntime(str(tmp_path), FakeManager({}, config_dir=tmp_path))
    commands = FakeCommands(runtime)
    commands._handle_mcp_command("")
    assert "No MCP servers configured" in commands.chat.everything
    assert "mcp.json" in commands.chat.everything


async def test_servers_are_listed_with_their_state(tmp_path):
    manager = FakeManager(
        {
            "good": FakeConnection("good", tool_count=3),
            "bad": FakeConnection("bad", state="failed", error="no such file"),
        },
        config_dir=tmp_path,
    )
    commands = FakeCommands(FakeRuntime(str(tmp_path), manager))
    commands._handle_mcp_command("")
    text = commands.chat.everything
    assert "good" in text and "connected (3 tools)" in text
    assert "no such file" in text


async def test_a_server_needing_auth_points_at_the_command(tmp_path):
    manager = FakeManager({"api": FakeConnection("api", state="needs-auth")}, config_dir=tmp_path)
    commands = FakeCommands(FakeRuntime(str(tmp_path), manager))
    commands._handle_mcp_command("")
    assert "/mcp signin" in commands.chat.everything


async def test_a_pending_authorization_url_is_shown(tmp_path):
    """A headless or browser-less run has to be able to recover the link."""
    manager = FakeManager(
        {
            "api": FakeConnection(
                "api", state="needs-auth", authorization_url="https://as.example.com/authorize?x=1"
            )
        },
        config_dir=tmp_path,
    )
    commands = FakeCommands(FakeRuntime(str(tmp_path), manager))
    commands._handle_mcp_command("")
    assert "https://as.example.com/authorize?x=1" in commands.chat.everything


async def test_config_errors_are_surfaced(tmp_path):
    manager = FakeManager({}, config_dir=tmp_path)
    manager.errors = ["mcp.json: bad shape"]
    commands = FakeCommands(FakeRuntime(str(tmp_path), manager))
    commands._handle_mcp_command("")
    assert "bad shape" in commands.chat.errors[0]


async def test_an_unknown_subcommand_shows_usage(tmp_path):
    commands = FakeCommands(FakeRuntime(str(tmp_path)))
    commands._handle_mcp_command("frobnicate")
    assert "Usage: /mcp" in commands.chat.everything
    assert "signin" in commands.chat.everything


# ---- sign in --------------------------------------------------------------


async def test_signin_opens_a_browser_and_prints_the_url(tmp_path, monkeypatch):
    """The URL is the reliable channel; a browser is only a convenience."""
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)

    manager = FakeManager({"api": FakeConnection("api", state="needs-auth")}, config_dir=tmp_path)
    commands = FakeCommands(FakeRuntime(str(tmp_path), manager))
    await commands._mcp_sign_in(commands.chat, "api")

    # The connection's own handler recorded the authorize URL, and it was shown.
    assert "Open this URL to authorize" in commands.chat.everything
    assert manager.signed_in == ["api"]


async def test_signin_falls_back_when_no_browser_opens(tmp_path, monkeypatch):
    monkeypatch.setattr("webbrowser.open", lambda url: False)
    manager = FakeManager({"api": FakeConnection("api", state="needs-auth")}, config_dir=tmp_path)
    commands = FakeCommands(FakeRuntime(str(tmp_path), manager))
    await commands._mcp_sign_in(commands.chat, "api")
    assert "Could not open a browser automatically" in commands.chat.everything


async def test_a_successful_signin_resyncs_the_tools(tmp_path):
    manager = FakeManager({"api": FakeConnection("api", state="needs-auth")}, config_dir=tmp_path)
    runtime = FakeRuntime(str(tmp_path), manager)
    commands = FakeCommands(runtime)
    await commands._mcp_sign_in(commands.chat, "api")
    assert manager.rebuilds == 1
    assert "signed in" in commands.chat.everything


async def test_a_failed_signin_reports_the_reason(tmp_path):
    manager = FakeManager(
        {"api": FakeConnection("api", state="needs-auth", sign_in_result=False)},
        config_dir=tmp_path,
    )
    commands = FakeCommands(FakeRuntime(str(tmp_path), manager))
    await commands._mcp_sign_in(commands.chat, "api")
    assert "sign-in failed" in commands.chat.errors[0]
    assert "registration refused" in commands.chat.errors[0]


async def test_signing_in_a_stdio_server_is_refused(tmp_path):
    manager = FakeManager({"local": FakeConnection("local", is_stdio=True)}, config_dir=tmp_path)
    commands = FakeCommands(FakeRuntime(str(tmp_path), manager))
    await commands._mcp_sign_in(commands.chat, "local")
    assert "local process" in commands.chat.everything
    assert manager.signed_in == []


async def test_signing_in_an_unknown_server_lists_the_real_ones(tmp_path):
    manager = FakeManager({"real": FakeConnection("real")}, config_dir=tmp_path)
    commands = FakeCommands(FakeRuntime(str(tmp_path), manager))
    await commands._mcp_sign_in(commands.chat, "imaginary")
    assert "Configured: real" in commands.chat.everything


# ---- sign out -------------------------------------------------------------


async def test_signout_clears_the_credentials(tmp_path):
    connection = FakeConnection("api", tokens={"access_token": "t"})
    manager = FakeManager({"api": connection}, config_dir=tmp_path)
    commands = FakeCommands(FakeRuntime(str(tmp_path), manager))
    await commands._mcp_sign_out(commands.chat, "api")
    assert connection.invalidated == ["all"]
    assert "credentials cleared" in commands.chat.everything


async def test_signout_on_an_unknown_server_says_so(tmp_path):
    commands = FakeCommands(FakeRuntime(str(tmp_path)))
    await commands._mcp_sign_out(commands.chat, "nope")
    assert "No MCP server named" in commands.chat.everything


# ---- a project's servers just run -----------------------------------------


def _project_file(tmp_path, servers: dict) -> None:
    path = tmp_path / ".vtx" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


async def test_a_project_server_is_listed_without_any_grant(tmp_path):
    """There is no trust step any more: a project's mcp.json is read on start,
    so a server it defines is running like any other, and `/mcp` says which file
    it came from -- the scope, not a permission."""
    _project_file(tmp_path, {"fs": {"command": "npx", "args": ["-y", "pkg"]}})
    manager = FakeManager(
        {"fs": FakeConnection("fs", state="connected", tool_count=3, scope="project")},
        config_dir=tmp_path,
    )
    commands = FakeCommands(FakeRuntime(str(tmp_path), manager))
    commands._handle_mcp_command("")
    body = commands.chat.everything
    assert "fs" in body and "[project]" in body and "connected (3 tools)" in body
    assert "trust" not in body.lower()


async def test_no_project_file_means_nothing_extra_is_said(tmp_path):
    commands = FakeCommands(FakeRuntime(str(tmp_path)))
    commands._handle_mcp_command("")
    assert "trust" not in commands.chat.everything.lower()


async def test_trust_is_not_a_subcommand_any_more(tmp_path):
    """A command that no longer exists has to fail as unknown, not silently
    do nothing: the muscle memory is the thing most likely to linger."""
    commands = FakeCommands(FakeRuntime(str(tmp_path)))
    commands._handle_mcp_command("trust")
    assert "Usage: /mcp" in commands.chat.everything
