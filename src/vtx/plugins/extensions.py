"""The extension-loading row.

Discovery is a plugin like anything else, which is the whole point: an extension
file is not in a parallel system, it is one row among the profile's. The four
discovery places are the ones the extension system always used, so an existing
``.vtx/extensions/`` keeps working with no migration:

1. project-local ``.vtx/extensions/*.py`` (and ``*/__init__.py``)
2. global ``~/.vtx/agent/extensions/*.py``
3. the ``extensions:`` list in ``config.yml`` and any ``--extension PATH``
4. installed-package entry points (``vtx.extensions``)

Composition happens before mount: :func:`discover` returns a :class:`Plugin` per
file, and :func:`boot` appends them to the profile's rows. They mount in the
same pass, in discovery order, and their teardown is the host's teardown.

Extensions load in-process with the user's own permissions. That is unchanged and
deliberate, and it is why a plugin you install is a plugin you must trust.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import shutil
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from vtx.core.paths import get_config_dir
from vtx.plugins.api import ExtensionActions, ExtensionAPI, ExtensionRecord
from vtx.plugins.plugin import Plugin, plugin

log = logging.getLogger("vtx.plugins.extensions")

#: Entry-point groups a package may declare to contribute extensions.
ENTRYPOINT_KEYS: tuple[str, ...] = ("vtx.extensions", "vtx.agent.extensions")

_ENTRYPOINT_KEYS_ATTR = "vtx_extension_entrypoint_keys"


class Config(BaseModel):
    """Whether the discovery row is active at all."""

    enabled: bool = True


@dataclass
class LoadedExtensions:
    """What discovery found, for ``/extensions`` and ``--list-extensions``.

    A record of the rows that mounted, not a second extension system: the
    handlers, tools, and commands live on the composition's own registries.
    """

    extensions: list[ExtensionRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    actions: ExtensionActions = field(default_factory=ExtensionActions)
    _bus: Any = field(default=None, repr=False, compare=False)

    def record(self, name: str, path: Path | None = None) -> ExtensionRecord:
        found = next((r for r in self.extensions if r.name == name), None)
        if found is None:
            # ``path`` is the file the row came from. Without it the record
            # falls back to the module name, which is what ``/extensions`` and
            # ``--list-extensions`` would then print in place of a real path.
            found = ExtensionRecord(name, path if path is not None else Path(name))
            self.extensions.append(found)
        return found

    def forget(self, name: str) -> bool:
        """Drop one extension's record. Used when its ``register`` failed."""
        for index, record in enumerate(self.extensions):
            if record.name == name:
                del self.extensions[index]
                return True
        return False

    @property
    def all_commands(self) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for record in self.extensions:
            merged.update(record.commands)
        return merged

    def list_extension_tools(self) -> list[Any]:
        tools: list[Any] = []
        for record in self.extensions:
            tools.extend(record.tools.values())
        return tools

    def local_tools_for(self, agent_name: str) -> list[Any]:
        out: list[Any] = []
        seen: set[str] = set()
        for record in self.extensions:
            for name, tool in record.local_tools.get(agent_name, {}).items():
                if name not in seen:
                    seen.add(name)
                    out.append(tool)
        return out

    def describe(self) -> list[dict[str, Any]]:
        return [record.describe() for record in self.extensions]

    @property
    def bus(self) -> Any:
        """The composition's event bus, in the adapter shape callers use.

        Resolved from the active composition so a handler registered through it
        lands on the one hub the rows emit through. With no composition mounted
        it is a standalone bus, which is what a unit test or a bare embedder
        gets.
        """
        from vtx.plugins.bootstrap import active_host
        from vtx.plugins.bus import ExtensionBus

        host = active_host()
        hub = host.hub if host is not None else None
        current = self._bus
        if current is None or (hub is not None and current._hub is not hub):
            current = ExtensionBus(hub) if hub is not None else ExtensionBus()
            self._bus = current
        return current


class ExtensionService:
    """``ctx.extensions`` — discovery, and the record of what it loaded."""

    def __init__(self) -> None:
        self.loaded = LoadedExtensions()
        self.plugins: list[Plugin] = []
        self.cwd: str = ""

    def load(
        self,
        *,
        cwd: str,
        auto_discover: bool = True,
        extra_paths: Sequence[str] = (),
        entry_points: bool = True,
        bus: Any = None,
    ) -> list[Plugin]:
        """Discover extensions and return one :class:`Plugin` per file.

        ``bus`` is the composition's event bus; bridging it here is what makes
        the transport-level ``before_provider_*`` hooks reachable from a plugin.
        """
        self.cwd = cwd
        self.plugins = []
        if bus is not None:
            from vtx.ai.provider_hooks import install_provider_bridge

            install_provider_bridge(bus)
        # ``auto_discover=False`` means "the caller named exactly what it wants",
        # so the two scan roots are skipped outright rather than filtered after
        # the fact. Filtering after the fact is what used to go wrong: a caller
        # who passed a *directory* had it expanded to the modules inside it,
        # then had every one of those modules discarded because none of them
        # equalled the directory it was named by.
        paths = discover_extension_paths(
            cwd=cwd,
            configured=list(extra_paths) if extra_paths else None,
            auto_discover=auto_discover,
        )
        for path in paths:
            self.plugins.append(self._plugin_for_path(path, cwd))
        if entry_points:
            for value in discover_entrypoints():
                self.plugins.append(self._plugin_for_module(value, cwd))
        return self.plugins

    def unload(self, host: Any) -> None:
        """Dispose every extension row mounted on ``host`` and clear the record.

        This is what makes a reload mean what it says: the previous pass's tools,
        handlers, and commands are unwound before the next one runs, so a removed
        extension is gone rather than merely shadowed.
        """
        for row_id in [r for r in host.mounted_ids() if r.startswith("extension:")]:
            host.unmount(row_id)
        self.loaded = LoadedExtensions()
        self.plugins = []

    def reload(
        self,
        host: Any,
        *,
        cwd: str,
        auto_discover: bool = True,
        extra_paths: Sequence[str] = (),
        entry_points: bool = True,
    ) -> list[Plugin]:
        """Unload the previous pass, discover again, and mount the new rows."""
        self.unload(host)
        plugins = self.load(
            cwd=cwd,
            auto_discover=auto_discover,
            extra_paths=extra_paths,
            entry_points=entry_points,
        )
        for spec in plugins:
            host.add(spec)
        if plugins:
            host.mount()
        return plugins

    def _plugin_for_path(self, path: Path, cwd: str) -> Plugin:
        loaded = self.loaded
        module_name = path.stem if path.name != "__init__.py" else path.parent.name

        def apply(ctx: Any, config: Any) -> Any:
            record = loaded.record(module_name, path)
            try:
                module = load_module(path, module_name)
                register = getattr(module, "register", None)
                if register is None:
                    raise AttributeError(f"{path} exposes no register(api) function")
                # The record is created only once ``register`` has run, so a
                # module that fails part-way leaves an error and no half-built
                # entry in the extension list.
                api = ExtensionAPI(module_name, ctx, record, loaded.actions, cwd=cwd)
                register(api)
            except Exception as exc:
                # Discard the half-built record so a failed module leaves an
                # error and no entry in the extension list.
                loaded.forget(module_name)
                loaded.errors.append(f"{path}: {exc}")
                log.warning("extension %s failed to load: %s", path, exc)

        return Plugin(
            name=f"extension:{module_name}",
            apply=apply,
            inject=("tools",),
            meta={"path": str(path)},
        )

    def _plugin_for_module(self, value: str, cwd: str) -> Plugin:
        loaded = self.loaded
        module_name = value.split(":")[0].replace(".", "_")

        def apply(ctx: Any, config: Any) -> Any:
            record = loaded.record(module_name)
            try:
                module_name_, _, attr = value.partition(":")
                module = importlib.import_module(module_name_)
                register = getattr(module, attr or "register", None)
                if register is None:
                    raise AttributeError(f"{value} exposes no register(api) function")
                # The record is created only once ``register`` has run, so a
                # module that fails part-way leaves an error and no half-built
                # entry in the extension list.
                api = ExtensionAPI(module_name, ctx, record, loaded.actions, cwd=cwd)
                register(api)
            except Exception as exc:
                # Discard the half-built record so a failed module leaves an
                # error and no entry in the extension list.
                loaded.forget(module_name)
                loaded.errors.append(f"{value}: {exc}")
                log.warning("extension %s failed to load: %s", value, exc)

        return Plugin(
            name=f"extension:{module_name}", apply=apply, inject=("tools",), meta={"path": value}
        )


def discover_extension_paths(
    *,
    cwd: str,
    configured: Iterable[str] | None = None,
    agent_dir: Path | None = None,
    auto_discover: bool = True,
) -> list[Path]:
    """Every extension path, project first, then global, then caller-configured.

    Configured entries are resolved relative to the invocation directory, and a
    directory that is itself a package contributes its ``__init__.py``. A plain
    configured directory contributes each module inside it.

    ``auto_discover=False`` drops the two scan roots — ``<cwd>/.vtx/extensions``
    and the agent directory — and resolves only what ``configured`` names.
    """
    configured_paths = [Path(p).expanduser() for p in (configured or [])]
    resolved_agent_dir = agent_dir or (get_config_dir() / "agent")

    seen: set[Path] = set()
    ordered: list[Path] = []

    def add(candidate: Path) -> None:
        try:
            resolved = candidate.resolve()
        except OSError:
            return
        if resolved in seen or not resolved.exists():
            return
        seen.add(resolved)
        ordered.append(resolved)

    if auto_discover:
        add_all(Path(cwd) / ".vtx" / "extensions", add)
        add_all(resolved_agent_dir / "extensions", add)
    for configured_path in configured_paths:
        if configured_path.is_dir():
            if (configured_path / "__init__.py").is_file():
                add(configured_path / "__init__.py")
                continue
            add_all(configured_path, add)
        elif configured_path.exists():
            add(configured_path)
    return ordered


def add_all(root: Path, add: Any) -> None:
    """Feed every extension file under ``root`` to ``add``."""
    for candidate in _sorted_py_files(root):
        add(candidate)


def discover_entrypoints() -> list[str]:
    """Module paths contributed by installed packages through entry points."""
    from importlib.metadata import entry_points

    found: list[str] = []
    for group in _entrypoint_keys():
        try:
            points = entry_points(group=group)
        except Exception:  # pragma: no cover - metadata is best-effort
            continue
        found.extend(point.value for point in points)
    return found


def _entrypoint_keys() -> list[str]:
    extra = getattr(sys, _ENTRYPOINT_KEYS_ATTR, None)
    if isinstance(extra, Iterable):
        return [*ENTRYPOINT_KEYS, *extra]
    return list(ENTRYPOINT_KEYS)


def register_extension_entrypoint_key(key: str) -> None:
    """Add an entry-point group an embedder's packages use."""
    current = getattr(sys, _ENTRYPOINT_KEYS_ATTR, None)
    keys = list(current) if isinstance(current, Iterable) else []
    if key not in keys:
        keys.append(key)
    setattr(sys, _ENTRYPOINT_KEYS_ATTR, keys)


def invalidate_extension_bytecode(path: Path) -> None:
    """Drop cached bytecode for an extension package so an edit is really seen.

    The entry module is compiled from source by :func:`load_module`, but a
    *package* extension's submodules are imported through the normal finder,
    which validates ``__pycache__`` against the source's mtime and size. An edit
    that keeps the same length and lands in the same mtime tick is therefore
    served from cache, and a reload would report success while running the old
    submodule. Removing the cache is safe: Python regenerates it on next import.
    """
    if path.name != "__init__.py":
        return
    cache = path.parent / "__pycache__"
    try:
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)
    except OSError:
        # A read-only tree is not a reason to fail the reload; the worst case is
        # the mtime/size caveat above.
        pass


class ExtensionLoadError(RuntimeError):
    """One extension module could not be imported or run.

    ``error`` is either the exception that caused it or a string describing what
    was wrong; either way the message starts with the path, so a failure printed
    among a dozen loads is still attributable.
    """

    def __init__(self, path: Path | str, error: BaseException | str) -> None:
        if isinstance(error, BaseException):
            message = f"Extension {path} failed to import: {error}"
        else:
            message = f"Extension {path}: {error}"
        super().__init__(message)
        self.path = str(path)
        self.error = error


def _sorted_py_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith((".", "_")) and entry.name != "__init__.py":
            continue
        if entry.is_file() and entry.suffix == ".py":
            out.append(entry)
        elif entry.is_dir() and (entry / "__init__.py").is_file():
            out.append(entry / "__init__.py")
    return out


#: The record's historical name. ``Extension`` is what the old module exported;
#: ``ExtensionRecord`` is what it is called now, and the alias keeps a caller
#: that reads ``extension.handlers`` working.
Extension = ExtensionRecord


def load_module(path: Path, name: str | None = None) -> Any:
    """Import one extension file or package by path.

    The source is compiled from the text just read rather than handed to
    ``spec.loader.exec_module``. That is not a style preference: the normal
    loader validates cached bytecode against the source's mtime and size, so an
    edit that keeps the file the same length and lands in the same mtime
    granularity window is served from ``__pycache__``. A hot reload would then
    report success while running the previous version, which is worse than not
    reloading at all.
    """
    target = Path(path)
    module_name = name or (target.stem if target.name != "__init__.py" else target.parent.name)
    # A stable, path-derived name, so a re-import replaces rather than stacks.
    module_name = f"vtx_ext_{module_name}"
    if target.name == "__init__.py":
        spec = importlib.util.spec_from_file_location(
            module_name, str(target), submodule_search_locations=[str(target.parent)]
        )
    else:
        spec = importlib.util.spec_from_file_location(module_name, str(target))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {target}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        source = target.read_text(encoding="utf-8")
    except OSError as exc:
        sys.modules.pop(module_name, None)
        raise ImportError(f"cannot read {target}: {exc}") from exc
    try:
        # module_from_spec already set __name__, __file__, and __path__ for a
        # package, so exec'ing the code object behaves exactly like an import.
        exec(compile(source, str(target), "exec"), module.__dict__)
    except Exception as exc:
        # A syntax error or a failing import must not leave a half-built module
        # in sys.modules that a later reload would then bind to.
        sys.modules.pop(module_name, None)
        raise ExtensionLoadError(target, exc) from exc
    return module


def load_extension(
    path: Path | str, *, cwd: str = "", session_file: str | None = None
) -> ExtensionRecord:
    """Import one extension module by path, run its ``register``, return the record.

    Kept for a caller that loads one module directly rather than mounting it as
    a row; :func:`ExtensionService.load` is the composition route, and this is
    the same work without a host.
    """
    target = Path(path)
    module_name = target.stem if target.name != "__init__.py" else target.parent.name
    record = ExtensionRecord(module_name, target)
    try:
        module = load_module(target, module_name)
    except Exception as exc:
        raise ExtensionLoadError(target, exc) from exc

    register = getattr(module, "register", None)
    if not callable(register):
        raise ExtensionLoadError(target, "does not export a top-level `register(api)` function")

    from vtx.plugins.api import ExtensionActions, ExtensionAPI

    api = ExtensionAPI(module_name, _standalone_ctx(record), record, ExtensionActions(), cwd=cwd)
    try:
        register(api)
    except Exception as exc:
        raise ExtensionLoadError(target, f"register(api) raised: {exc}") from exc
    return record


def load_for_runtime(
    cwd: str, *, extra_paths: Sequence[str] | None = None, **_: Any
) -> LoadedExtensions:
    """The discovery record of the composition already running.

    Kept because the TUI, the headless runner, and the CLI all used to call this
    to build a ``LoadedExtensions``. Discovery now happens in
    :func:`vtx.plugins.bootstrap.boot`, before mount, so this returns what that
    already found. ``extra_paths`` is accepted and ignored: a caller that needs
    an extra extension passes it to ``boot`` instead, because by the time a
    runtime exists the composition is already mounted.
    """
    from vtx.plugins.bootstrap import active_host

    host = active_host()
    if host is not None and "extensions" in host.base:
        return host.base.extensions.loaded
    return LoadedExtensions()


def reload_extensions(
    *, cwd: str, extra_paths: Sequence[str] | None = None, auto_discover: bool = True
) -> LoadedExtensions:
    """Re-discover extensions into the running composition, in place.

    Unloads the previous pass first, so a removed extension's tools and handlers
    are actually gone rather than shadowed by the new ones.
    """
    from vtx.plugins.bootstrap import active_host

    host = active_host()
    if host is None or "extensions" not in host.base:
        return LoadedExtensions()
    service = host.base.extensions
    service.reload(host, cwd=cwd, auto_discover=auto_discover, extra_paths=list(extra_paths or ()))
    return service.loaded


def _standalone_ctx(record: ExtensionRecord) -> Any:
    """A context whose tool service reads straight from ``record``.

    Used by :func:`load_extension` and :func:`load_all_extensions`, which load a
    module without a composition and so have nowhere to publish a tool registry.
    """
    from vtx.plugins.context import Context
    from vtx.plugins.services import ToolService

    tools = ToolService()
    tools._owned = record.tools
    ctx = Context()
    ctx._services["tools"] = tools
    return ctx


def load_all_extensions(
    *, cwd: str, extra_paths: Sequence[str] | None = None, auto_discover: bool = True
) -> tuple[list[ExtensionRecord], list[str], LoadedExtensions]:
    """Discover and load extensions without mounting a composition.

    Returns ``(records, errors, loaded)``. Loading is eager because a caller
    asking for records wants them now; the same plugin objects are what
    :func:`ExtensionService.load` returns for a composition to mount, so a caller
    that goes on to mount them should use that instead and let the host run
    ``register`` exactly once.
    """
    from vtx.plugins.host import PluginHost
    from vtx.plugins.profile import DEFAULT_PROFILE, load_profile

    service = ExtensionService()
    plugins = service.load(
        cwd=cwd, auto_discover=auto_discover, extra_paths=list(extra_paths or ())
    )
    # A host over the real base rows, so the extensions' ``inject`` finds every
    # service. Discovery is not run by this host — ``plugins`` already is the
    # discovered set — so nothing is loaded twice.
    host = PluginHost(load_profile(DEFAULT_PROFILE), label="extension-probe")
    for spec in plugins:
        host.add(spec)
    host.mount()
    host.dispose()
    return service.loaded.extensions, list(service.loaded.errors), service.loaded


@plugin(name="extensions", inject=("tools",), config=Config)
def apply(ctx, config: Config):
    """Publish the discovery seam.

    The discovered rows themselves are appended to the composition before mount
    (see :func:`ExtensionService.load` and :func:`vtx.plugins.bootstrap.boot`);
    this row publishes the service they all write their record into.
    """
    existing = ctx.get("extensions")
    if existing is not None:
        # ``boot`` created it up front so discovery could run before mount; keep
        # the object holding the record rather than mounting an empty one.
        return existing
    ctx.provide("extensions", ExtensionService())


PLUGIN = apply
