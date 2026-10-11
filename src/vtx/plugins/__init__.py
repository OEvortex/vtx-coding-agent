"""The plugin kernel: one composition is a list of addressable plugin rows.

A plugin names the services it needs instead of importing the things that
provide them, so anything in this package can be replaced from a row. The
minimal shape::

    from vtx.plugins import plugin, PluginHost

    @plugin(name="my-tool", inject=("tools",))
    def apply(ctx, config):
        ctx.tools.register(MyTool())

Composition, mounting, and layering::

    from vtx.plugins import load_profile, PluginHost

    rows = load_profile("headless", patches=["/tmp/extra.yml"])
    host = PluginHost(rows)
    host.mount()
    ctx = host.base
    ...
    host.dispose()
"""

from __future__ import annotations

from vtx.plugins.bootstrap import active_context, active_host, boot, reset, shutdown
from vtx.plugins.builtins.tools._register import mount_tool
from vtx.plugins.context import STANDARD_KEYS, Context
from vtx.plugins.errors import (
    ConfigError,
    CycleError,
    DuplicateRowError,
    EventModeError,
    PatchError,
    PluginError,
    RowError,
    ServiceMissingError,
)
from vtx.plugins.events import DispatchMode, EventHub
from vtx.plugins.host import PluginHost, compose
from vtx.plugins.loader import (
    Row,
    apply_patch,
    load_yaml,
    load_yaml_file,
    resolve_plugin,
    rows_from_layer,
    validate_config,
)
from vtx.plugins.plugin import Plugin, Service, apply_plugin, as_plugin, plugin
from vtx.plugins.profile import (
    DEFAULT_PROFILE,
    dump_config,
    load_profile,
    profile_dir,
    profiles_dir,
)

__all__ = [
    "DEFAULT_PROFILE",
    "STANDARD_KEYS",
    "ConfigError",
    "Context",
    "CycleError",
    "DispatchMode",
    "DuplicateRowError",
    "EventHub",
    "EventModeError",
    "PatchError",
    "Plugin",
    "PluginError",
    "PluginHost",
    "Row",
    "RowError",
    "Service",
    "ServiceMissingError",
    "active_context",
    "active_host",
    "apply_patch",
    "apply_plugin",
    "as_plugin",
    "boot",
    "compose",
    "dump_config",
    "load_profile",
    "load_yaml",
    "load_yaml_file",
    "mount_tool",
    "plugin",
    "profile_dir",
    "profiles_dir",
    "reset",
    "resolve_plugin",
    "rows_from_layer",
    "shutdown",
    "validate_config",
]
