"""Booting a composition, and the one the process is currently running.

``boot`` mounts a profile and keeps the host reachable for the lifetime of the
run. Everything downstream reads seams from it rather than importing
implementations, which is what makes the row list authoritative: the row list is
what decided which ``ctx.tools`` you have.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from vtx.plugins.context import Context
from vtx.plugins.host import PluginHost
from vtx.plugins.profile import DEFAULT_PROFILE, load_profile

_ACTIVE: PluginHost | None = None


def boot(
    profile: str | None = None,
    patches: Sequence[str | Path] | None = None,
    *,
    cwd: str | None = None,
    extension_paths: Sequence[str] = (),
    auto_discover_extensions: bool = True,
) -> PluginHost:
    """Mount a profile and make its host the active one.

    ``patches`` are applied after the profile's own layer, in order.
    ``extension_paths`` and ``auto_discover_extensions`` feed the extension
    discovery row, which runs before mount so its rows mount in the same pass.
    """
    global _ACTIVE
    name = profile or os.environ.get("VTX_PROFILE") or DEFAULT_PROFILE
    rows = load_profile(name, patches)
    host = PluginHost(rows, label=name)
    service = _extension_service(host)
    working = cwd if cwd is not None else os.getcwd()
    discovered = service.load(
        cwd=working, auto_discover=auto_discover_extensions, extra_paths=extension_paths
    )
    # Publish it now, so the ``extensions`` row adopts this object rather than
    # mounting a second, empty one beside it.
    host.base.provide("extensions", service)
    for spec in discovered:
        host.add(spec)
    host.mount()
    _ACTIVE = host
    return host


def _extension_service(host: PluginHost) -> Any:
    """The host's discovery service, creating one when no row provides it yet."""
    from vtx.plugins.extensions import ExtensionService

    service = getattr(host, "_extension_service", None)
    if service is None:
        service = ExtensionService()
        host._extension_service = service
    return service


def active_host() -> PluginHost | None:
    return _ACTIVE


def active_context() -> Context | None:
    return _ACTIVE.base if _ACTIVE is not None else None


def reset() -> None:
    """Dispose the active composition. Used by tests and by a reload."""
    global _ACTIVE
    if _ACTIVE is not None:
        _ACTIVE.dispose()
    _ACTIVE = None


def shutdown() -> None:
    reset()


__all__ = ["DEFAULT_PROFILE", "active_context", "active_host", "boot", "reset", "shutdown"]
