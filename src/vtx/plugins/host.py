"""The host: mount a row list into a live composition.

Activation is service-availability driven, exactly like the rows say it is. Row
order carries no load semantics — a row mounts as soon as every service it names
exists, and a row whose service never appears fails the whole mount naming the
missing key rather than being skipped quietly.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from vtx.plugins.context import Context
from vtx.plugins.errors import ServiceMissingError
from vtx.plugins.events import EventHub
from vtx.plugins.loader import Row, resolve_plugin, validate_config
from vtx.plugins.plugin import Plugin, apply_plugin, as_plugin

MountHook = Any


class PluginHost:
    """One mounted composition, with its own :class:`Context` and teardown."""

    def __init__(
        self,
        rows: Sequence[Row],
        *,
        hub: EventHub | None = None,
        base: Context | None = None,
        label: str = "",
        resolver: Callable[[str], Plugin] | None = None,
    ) -> None:
        self.rows = list(rows)
        self.hub = hub or EventHub()
        #: The root. It owns the composition-wide service table and any
        #: composition-wide effects; each row hangs off it as a scope, so a
        #: row's teardown unwinds only that row.
        self.base = base or Context(hub=self.hub, label=label or "root")
        #: ``name -> Plugin``. Defaults to the import resolver; a caller
        #: embedding plugin objects passes its own.
        self._resolver = resolver or resolve_plugin
        #: Plugins handed over as objects rather than import paths.
        self._inline: dict[str, Plugin] = {}
        #: The discovery service ``boot`` installs before mounting, so extension
        #: rows can be discovered into this host rather than a second one.
        self._extension_service: Any = None
        self.mounted: list[tuple[Row, Plugin, Context, Any]] = []

    def add(self, plugin_obj: Any, *, row_id: str | None = None) -> Row:
        """Append a plugin object (not an import path) as a row.

        A row with the same id is taken over: the caller is substituting its own
        provider for that row, so it is re-enabled and pointed at the plugin
        object rather than at whatever import path it had.
        """
        spec = as_plugin(plugin_obj)
        self._inline[spec.name] = spec
        rid = row_id or spec.name
        for index, row in enumerate(self.rows):
            if row.id == rid:
                self.rows[index] = Row(id=rid, name=spec.name, config=row.config)
                return self.rows[index]
        row = Row(id=rid, name=spec.name)
        self.rows.append(row)
        return row

    def _resolve(self, name: str) -> Plugin:
        inline = self._inline.get(name)
        return inline if inline is not None else self._resolver(name)

    # ---- mounting -------------------------------------------------------

    def mount(self) -> Context:
        """Mount every enabled row. Returns the root context."""
        pending = [row for row in self.rows if not row.is_disabled(self.base)]
        specs = {row.id: self._resolve(row.name) for row in pending}
        remaining = list(pending)
        while remaining:
            progressed = False
            for row in list(remaining):
                spec = specs[row.id]
                if any(key not in self.base for key in spec.inject):
                    continue
                self._mount_one(row, spec)
                remaining.remove(row)
                progressed = True
            if not progressed:

                def _needs(row: Row) -> str:
                    keys = [key for key in specs[row.id].inject if key not in self.base]
                    return f"{row.id} (needs {', '.join(keys)})"

                blockers = ", ".join(_needs(row) for row in remaining)
                raise ServiceMissingError("these rows can never mount: " + blockers)
        return self.base

    def _mount_one(self, row: Row, spec: Plugin) -> None:
        config = validate_config(spec, row)
        scope = Context(parent=self.base, label=row.id, service_owner=self.base)
        apply_plugin(spec, scope, config)
        self.mounted.append((row, spec, scope, config))

    # ---- teardown -------------------------------------------------------

    def dispose(self) -> None:
        """Unmount in reverse order, so providers outlive their consumers."""
        for _row, _spec, scope, _config in reversed(self.mounted):
            scope.dispose()
        self.mounted.clear()

    def unmount(self, row_id: str) -> bool:
        """Dispose one row. Returns ``False`` when it was not mounted."""
        for index, (row, _spec, scope, _config) in enumerate(self.mounted):
            if row.id == row_id:
                scope.dispose()
                self.mounted.pop(index)
                return True
        return False

    # ---- introspection --------------------------------------------------

    def row_ids(self) -> list[str]:
        return [row.id for row in self.rows]

    def mounted_ids(self) -> list[str]:
        return [row.id for row, _spec, _scope, _config in self.mounted]

    def status(self) -> list[dict[str, Any]]:
        """One record per row, for ``--dump-config`` and the plugin list."""
        mounted = {row.id: True for row, _s, _c, _cfg in self.mounted}
        disabled = {row.id for row in self.rows if row.is_disabled(self.base)}
        return [
            {
                "id": row.id,
                "name": row.name,
                "disabled": row.id in disabled,
                "mounted": mounted.get(row.id, False),
                "config": dict(row.config),
            }
            for row in self.rows
        ]


def compose(
    rows: Iterable[Row],
    *,
    hub: EventHub | None = None,
    base: Context | None = None,
    label: str = "",
) -> PluginHost:
    """Convenience: build a host and mount it in one step."""
    host = PluginHost(list(rows), hub=hub, base=base, label=label)
    host.mount()
    return host


__all__ = ["PluginHost", "compose"]
