"""Failures raised by the plugin kernel.

Every one of these is a configuration or wiring error: none is a bug the user
should have to read a traceback to discover. ``PluginError`` messages name the
offending row, service key, or event, because the node it prints is the only
place the operator can act on.
"""

from __future__ import annotations


class PluginError(Exception):
    """Base class for every plugin-kernel failure."""


class RowError(PluginError):
    """A plugin row is malformed, or names something that cannot be resolved."""


class ConfigError(PluginError):
    """A plugin row's ``config`` does not satisfy the plugin's declared schema."""


class DuplicateRowError(PluginError):
    """Two rows in one layer claim the same id."""


class ServiceMissingError(PluginError):
    """A plugin names a service key no provider supplies."""


class CycleError(PluginError):
    """Plugin rows depend on each other in a way no mount order satisfies."""


class EventModeError(PluginError):
    """An event name is declared with two different dispatch modes."""


class PatchError(PluginError):
    """A patch layer is malformed, or targets a row that does not exist."""
