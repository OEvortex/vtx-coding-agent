"""Background startup chores: binary download, update check, file-path scan,
git-branch refresh and launch warnings."""

from __future__ import annotations

import asyncio
import glob
import os
from typing import TYPE_CHECKING, Any, Literal

from vtx.agent.tools_manager import ensure_tools
from vtx.core.config import update_available_binaries
from vtx.core.update_check import get_newer_pypi_version
from vtx.core.version import PACKAGE_NAME, VERSION
from vtx.tui.blocks import LaunchWarning
from vtx.tui.chat import ChatLog
from vtx.tui.input import InputBox
from vtx.tui.widgets import InfoBar, format_path

_CHANGELOG_URL = "https://github.com/OEvortex/vtx-coding-agent/blob/main/CHANGELOG.md"


class StartupMixin:
    _cwd: str
    _fd_path: str | None
    _is_running: bool
    _startup_complete: bool
    _update_notice_shown: bool
    _pending_update_notice_version: str | None
    _git_branch_refresh_inflight: bool
    _launch_warnings: list[LaunchWarning]
    _runtime: Any

    if TYPE_CHECKING:
        query_one: Any
        call_later: Any

    async def _refresh_git_branch(self) -> None:
        # Skip the tick if the previous refresh is still resolving in its thread.
        if self._git_branch_refresh_inflight:
            return
        self._git_branch_refresh_inflight = True
        try:
            info_bar = self.query_one("#compact-footer", InfoBar)
            await info_bar.refresh_git_branch()
        finally:
            self._git_branch_refresh_inflight = False

    def _scan_file_paths(self) -> list[str]:
        patterns = [
            "**/*.py",
            "**/*.js",
            "**/*.ts",
            "**/*.tsx",
            "**/*.json",
            "**/*.md",
            "**/*.yaml",
            "**/*.yml",
            "**/*.toml",
        ]
        paths = []
        for pattern in patterns:
            for path in glob.glob(os.path.join(self._cwd, pattern), recursive=True):
                rel_path = os.path.relpath(path, self._cwd)
                if not rel_path.startswith(
                    (".git", "node_modules", "__pycache__", ".venv", "venv")
                ):
                    paths.append(rel_path)
        return sorted(paths)

    async def _collect_file_paths(self) -> None:
        """Collect file paths using glob (fallback when fd is unavailable)."""
        # The recursive glob can take seconds on large repos; keep it off the event loop.
        paths = await asyncio.to_thread(self._scan_file_paths)
        self.query_one("#input-box", InputBox).set_file_paths(paths)

    async def _ensure_binaries(self) -> None:
        paths = await ensure_tools(silent=True)
        update_available_binaries()

        if not self._fd_path and paths.get("fd"):
            self._fd_path = paths["fd"]
            self.query_one("#input-box", InputBox).set_fd_path(self._fd_path)

    def _apply_mcp_project_trust(self) -> None:
        """Let a previously trusted project's ``.vtx/mcp.json`` take effect.

        Only a decision the user already made by hand is honored here. A
        project that has never been trusted stays untrusted, and ``/mcp`` says
        so rather than the file being silently ignored.
        """
        from vtx.mcp.config import project_config_path
        from vtx.mcp.trust import ProjectTrustStore

        try:
            trusted = ProjectTrustStore().is_trusted(self._runtime.cwd)
        except OSError as exc:
            self._add_launch_warning(f"MCP project trust: {exc}", severity="warning")
            return
        if trusted:
            self._runtime.set_project_trusted(True)
            return
        if project_config_path(self._runtime.cwd).is_file():
            self._add_launch_warning(
                "This project has a .vtx/mcp.json but is not trusted, so its servers "
                "were not started. Run /mcp trust to see what it would launch.",
                severity="warning",
            )

    async def _connect_mcp(self) -> None:
        """Connect the configured MCP servers and fold in their tools.

        Problems are reported once, as launch warnings, rather than as an error
        dialog: a server that will not start should not stop the session, and
        the user needs to know *which* one failed and why. ``/mcp`` shows the
        same state on demand.
        """
        self._apply_mcp_project_trust()
        try:
            tools = await self._runtime.connect_mcp()
        except Exception as exc:
            self._add_launch_warning(f"MCP: {exc}", severity="error")
            return

        manager = self._runtime.ensure_mcp_manager()
        for message in manager.errors:
            self._add_launch_warning(f"MCP config: {message}", severity="error")
        for status in manager.statuses():
            if status.state == "failed":
                self._add_launch_warning(f"MCP server {status.name!r}: {status.error or 'failed'}")
            elif status.state == "needs-auth":
                self._add_launch_warning(
                    f"MCP server {status.name!r} needs sign-in; run /mcp signin"
                )

        if tools:
            # The session header lists the tool surface, so a tool that arrived
            # after startup has to be reflected there or /session under-reports
            # what the model can actually call.
            self.call_later(self._refresh_loaded_resources)

    def _refresh_loaded_resources(self) -> None:
        context = self._runtime.context
        if context is None:
            return
        try:
            chat = self.query_one("#chat-log", ChatLog)
            chat.add_loaded_resources(
                context_paths=[format_path(f.path) for f in context.agents_files],
                skills=context.skills,
                tools=self._runtime.tools,
            )
        except Exception:
            # Purely cosmetic: never let a redraw failure break a session.
            pass

    async def _check_for_updates(self) -> None:
        latest = await get_newer_pypi_version(PACKAGE_NAME, VERSION)
        if latest is None:
            return

        self._pending_update_notice_version = latest
        self.call_later(self._show_pending_update_notice_if_idle)

    def _show_pending_update_notice_if_idle(self) -> None:
        if not self._startup_complete or self._is_running:
            return
        if self._update_notice_shown or self._pending_update_notice_version is None:
            return

        chat = self.query_one("#chat-log", ChatLog)
        chat.add_update_available_message(
            self._pending_update_notice_version, changelog_url=_CHANGELOG_URL
        )
        self._update_notice_shown = True
        self._pending_update_notice_version = None

    def _add_launch_warning(
        self, message: str, *, severity: Literal["warning", "error"] = "warning"
    ) -> None:
        cleaned = message.strip()
        if not cleaned:
            return
        self._launch_warnings.append(LaunchWarning(message=cleaned, severity=severity))

    def _flush_launch_warnings(self, chat: ChatLog) -> None:
        if self._launch_warnings:
            chat.add_launch_warnings(self._launch_warnings)

    async def _ensure_models_dev(self) -> None:
        import contextlib

        from vtx.ai.dynamic_models import _fetch_models_dev

        with contextlib.suppress(Exception):
            await _fetch_models_dev()
