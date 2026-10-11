"""Plugin rows, patch layers, and the loader that composes them.

A composition is a flat list of :class:`Row`, each addressed by a unique ``id``.
Layers apply in order onto an empty list; the last write for an id wins. A row's
``config`` is a whole value, not a merge — a row that differs per mode belongs to
that mode's layer, which keeps every row owned by exactly one layer plus the
user's.

YAML rows look like::

    - id: llm
      name: vtx.plugins.builtins.llm:PLUGIN
      config:
        default_provider: deepseek

``name`` is an importable path: ``module:attr`` or just ``module``, which resolves
to the module's ``PLUGIN`` export. ``disabled`` may be a literal boolean or a
dotted path to a predicate called with the :class:`Context`.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import yaml
from pydantic import ValidationError

from vtx.plugins.errors import ConfigError, DuplicateRowError, PatchError, RowError
from vtx.plugins.plugin import Plugin, as_plugin

RowDict = Mapping[str, Any]
PatchDict = Mapping[str, Any]


class _CallableString(str):
    """A YAML string that resolves to a callable at load time."""


def _callable_constructor(loader: yaml.SafeLoader, node: yaml.Node) -> _CallableString:
    value = loader.construct_scalar(node)
    if not value:
        raise RowError("!callable needs a dotted path, e.g. !callable mypkg.pred:is_prod")
    return _CallableString(value)


class RowLoader(yaml.SafeLoader):
    """SafeLoader plus the ``!callable`` tag for dynamic row predicates."""


RowLoader.add_constructor("!callable", _callable_constructor)


def load_yaml(text: str) -> Any:
    return yaml.load(text, Loader=RowLoader)


def load_yaml_file(path: Any) -> Any:
    with open(path, encoding="utf-8") as handle:
        return load_yaml(handle.read())


def resolve_callable(spec: Any) -> Callable[..., Any] | None:
    """Resolve a dotted ``module:attr`` path. Returns ``None`` for anything else."""
    if callable(spec):
        return spec
    if not isinstance(spec, str):
        return None
    module_name, _, attr = spec.partition(":")
    if ":" in spec:
        module = importlib.import_module(module_name)
        target = getattr(module, attr)
    else:
        target = importlib.import_module(module_name)
    if not callable(target):
        raise RowError(f"{spec!r} resolved to {target!r}, which is not callable")
    return target


def resolve_plugin(name: str) -> Plugin:
    """Resolve a row's ``name`` to a :class:`Plugin`."""
    module_name, sep, attr = name.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise RowError(f"cannot import plugin module {module_name!r}: {exc}") from exc
    target = getattr(module, attr, None) if sep else getattr(module, "PLUGIN", None)
    if target is None and not sep:
        # A module whose apply function is the module itself is not a thing;
        # look for any Plugin-looking export before giving up.
        for candidate in vars(module).values():
            if isinstance(candidate, Plugin):
                target = candidate
                break
    if target is None:
        raise RowError(
            f"plugin module {module_name!r} exposes no {'PLUGIN' if not sep else attr!r}"
        )
    try:
        return as_plugin(target)
    except TypeError as exc:
        raise RowError(f"cannot read {name!r} as a plugin: {exc}") from exc


@dataclass(frozen=True)
class Row:
    """One addressable plugin row in a composition."""

    id: str
    name: str
    disabled: Any = False
    config: Mapping[str, Any] = field(default_factory=dict)
    meta: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "disabled": bool(self.disabled),
            "config": dict(self.config),
        }
        if self.meta:
            out["meta"] = dict(self.meta)
        return out

    def is_disabled(self, ctx: Any = None) -> bool:
        """Evaluate this row's ``disabled`` value against ``ctx``."""
        if not self.disabled:
            return False
        if isinstance(self.disabled, bool):
            return self.disabled
        predicate = resolve_callable(self.disabled)
        if predicate is None:
            return bool(self.disabled)
        if ctx is None:
            return False
        return bool(predicate(ctx))


def _row_from_dict(entry: RowDict, *, layer: str, partial: bool = False) -> Row:
    if not isinstance(entry, Mapping):
        raise RowError(f"{layer}: a row must be a mapping, got {entry!r}")
    row_id = entry.get("id")
    if not row_id or not isinstance(row_id, str):
        raise RowError(f"{layer}: a row needs a non-empty string `id`")
    name = entry.get("name")
    if not partial and (not name or not isinstance(name, str)):
        raise RowError(f"{layer}: row {row_id!r} needs a `name` import path")
    known = {"id", "name", "disabled", "config", "meta"}
    unknown = set(entry) - known
    if unknown:
        raise RowError(f"{layer}: row {row_id!r} has unknown keys {sorted(unknown)}")
    return Row(
        id=row_id,
        name=name if isinstance(name, str) else "",
        disabled=entry.get("disabled", False),
        config=dict(entry.get("config") or {}),
        meta=dict(entry.get("meta") or {}),
    )


def rows_from_layer(layer: Any, *, layer_name: str = "layer") -> list[Row]:
    """Normalise one YAML layer into :class:`Row` objects.

    A layer is either a bare list of full row mappings or a list of single-key
    patch operations (``insert``, ``set``, ``replace``, ``remove``). ``override``
    is rejected here: a bundle layer starts from an empty list, so there is
    nothing to override.
    """
    if layer is None:
        return []
    if isinstance(layer, Mapping):
        # Allow {"insert": [...]} as the whole layer.
        layer = [layer]
    if not isinstance(layer, Sequence) or isinstance(layer, (str, bytes)):
        raise PatchError(f"{layer_name}: a layer must be a list, got {type(layer).__name__}")

    rows: list[Row] = []
    seen: set[str] = set()
    for entry in layer:
        if not isinstance(entry, Mapping):
            raise PatchError(f"{layer_name}: entries must be mappings, got {entry!r}")
        ops = {k for k in entry if k in _OPS}
        if not ops:
            row = _row_from_dict(entry, layer=layer_name)
            if row.id in seen:
                raise DuplicateRowError(f"{layer_name}: duplicate row id {row.id!r}")
            seen.add(row.id)
            rows.append(row)
            continue
        if len(ops) != 1:
            raise PatchError(f"{layer_name}: one operation per entry, got {sorted(ops)}")
        op = next(iter(ops))
        if op == "override":
            raise PatchError(
                f"{layer_name}: `override` has no meaning in a bundle layer, which starts "
                "from an empty row list; use `insert`"
            )
        payload = entry[op]
        if op in ("insert", "set", "replace"):
            if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
                raise PatchError(f"{layer_name}: {op} takes a list of rows")
            for raw in payload:
                row = _row_from_dict(raw, layer=f"{layer_name}/{op}")
                if row.id in seen:
                    raise DuplicateRowError(f"{layer_name}: duplicate row id {row.id!r}")
                seen.add(row.id)
                rows.append(row)
        elif op == "remove":
            targets = payload if isinstance(payload, Sequence) else [payload]
            for target in targets:
                if target not in seen:
                    raise PatchError(f"{layer_name}: cannot remove unknown row {target!r}")
                seen.discard(target)
            rows = [row for row in rows if row.id not in set(targets)]
    return rows


_OPS = ("insert", "set", "replace", "override", "remove")


def _override_row(existing: Row, raw: RowDict, *, layer: str) -> Row:
    """Merge ``raw`` onto ``existing``, replacing only the fields it names."""
    if "name" in raw and raw["name"] != existing.name:
        raise PatchError(f"{layer}: override cannot rename row {existing.id!r}; use replace")
    return Row(
        id=existing.id,
        name=raw.get("name", existing.name),
        disabled=raw.get("disabled", existing.disabled),
        config={**existing.config, **(raw.get("config") or {})},
        meta={**existing.meta, **(raw.get("meta") or {})},
    )


def apply_patch(rows: list[Row], patch: Any, *, layer_name: str = "patch") -> list[Row]:
    """Apply one patch layer onto ``rows``; returns a new list.

    ``insert`` appends a row unless the id exists, in which case it is a whole
    replacement (the last write for an id wins). ``set`` is the same, spelled for
    readability at the top of a layer. ``replace`` requires the id to exist.
    ``override`` requires the id to exist and replaces only the fields it names,
    with ``config`` merged rather than replaced. ``remove`` drops it.

    A bare list of full row mappings is treated as ``insert``.
    """
    patched = list(rows)
    if patch is None:
        return patched
    if not isinstance(patch, Sequence) or isinstance(patch, (str, bytes)):
        raise PatchError(f"{layer_name}: a patch layer must be a list")
    by_id = {row.id: row for row in patched}
    for entry in patch:
        if not isinstance(entry, Mapping):
            raise PatchError(f"{layer_name}: entries must be mappings, got {entry!r}")
        ops = {k for k in entry if k in _OPS}
        if not ops:
            row = _row_from_dict(entry, layer=layer_name)
            by_id[row.id] = row
            continue
        if len(ops) != 1:
            raise PatchError(f"{layer_name}: one operation per entry, got {sorted(ops)}")
        op = next(iter(ops))
        payload = entry[op]
        if op in ("insert", "set", "replace", "override"):
            if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
                raise PatchError(f"{layer_name}: {op} takes a list of rows")
            for raw in payload:
                row = _row_from_dict(raw, layer=f"{layer_name}/{op}", partial=(op == "override"))
                if op == "override":
                    if row.id not in by_id:
                        raise PatchError(f"{layer_name}: override targets unknown row {row.id!r}")
                    by_id[row.id] = _override_row(by_id[row.id], raw, layer=layer_name)
                    continue
                if op == "replace" and row.id not in by_id:
                    raise PatchError(f"{layer_name}: replace targets unknown row {row.id!r}")
                by_id[row.id] = row
        else:
            targets = payload if isinstance(payload, Sequence) else [payload]
            for target in targets:
                if target not in by_id:
                    raise PatchError(f"{layer_name}: cannot remove unknown row {target!r}")
                del by_id[target]
    # Preserve the incoming order, then append anything new in patch order.
    ordered_ids = [row.id for row in patched if row.id in by_id]
    ordered_ids += [rid for rid in by_id if rid not in set(ordered_ids)]
    return [by_id[rid] for rid in ordered_ids]


def validate_config(spec: Plugin, row: Row) -> Any:
    """Validate ``row.config`` against ``spec.config``.

    Returns the pydantic model instance, or ``None`` when the plugin declares no
    config schema. A plugin with no declared schema accepts any mapping.
    """
    model = spec.config
    if model is None:
        return row.config
    try:
        return model.model_validate(dict(row.config))
    except ValidationError as exc:
        raise ConfigError(
            f"row {row.id!r} has invalid config for plugin {spec.name!r}: {exc.error_count()} "
            f"error(s)\n" + "\n".join(f"  {e['loc']}: {e['msg']}" for e in exc.errors())
        ) from exc


def iter_rows(rows: Iterable[Row]) -> Iterable[Row]:
    yield from rows


__all__ = [
    "PatchError",
    "Row",
    "RowLoader",
    "apply_patch",
    "iter_rows",
    "load_yaml",
    "load_yaml_file",
    "resolve_callable",
    "resolve_plugin",
    "rows_from_layer",
    "validate_config",
]
