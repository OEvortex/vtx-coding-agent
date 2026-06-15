"""Background startup chores: binary download, update check, file-path scan,
git-branch refresh and launch warnings."""

from __future__ import annotations

import asyncio
import glob
import os
from typing import TYPE_CHECKING, Any, Literal

from vtx import update_available_binaries
from vtx.tools_manager import ensure_tools
from vtx.version import PACKAGE_NAME, VERSION

from ..update_check import get_newer_pypi_version
from .blocks import LaunchWarning
from .chat import ChatLog
from .input import InputBox
from .widgets import InfoBar

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

    if TYPE_CHECKING:
        query_one: Any
        call_later: Any

    async def _refresh_git_branch(self) -> None:
        # Skip the tick if the previous refresh is still resolving in its thread.
        if self._git_branch_refresh_inflight:
            return
        self._git_branch_refresh_inflight = True
        try:
            info_bar = self.query_one("#info-bar", InfoBar)
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

        from ..llm.dynamic_models import _fetch_models_dev

        with contextlib.suppress(Exception):
            await _fetch_models_dev()
