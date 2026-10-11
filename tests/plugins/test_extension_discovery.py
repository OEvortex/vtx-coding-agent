"""Focused tests for the extension discovery row.

The old 2600-line extension module was replaced by three modules under
``vtx.plugins``. The behaviour that matters is narrow and easy to state, which is
why these tests exist rather than the migrated ones: an extension file has to be
*found*, has to be *re-read* when it is edited, and has to *fail loudly and
locally* when it is broken. Every root used here is under ``tmp_path``, and every
call that would otherwise read ``get_config_dir()`` passes ``agent_dir=``, so no
test can touch the developer's real ``~/.vtx``.

One source bug is worked around rather than fixed here, and it is the reason for
the autouse fixture below: the handler-arity caches are keyed by ``id(handler)``,
and CPython hands a freed function's address straight back to the next one, so a
cache entry written by one test's short-lived handler answers for an unrelated
handler in another test.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import os
import py_compile
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from vtx.plugins import PluginHost, boot, plugin, reset
from vtx.plugins.extensions import (
    _ENTRYPOINT_KEYS_ATTR,
    ENTRYPOINT_KEYS,
    ExtensionLoadError,
    ExtensionService,
    _entrypoint_keys,
    discover_entrypoints,
    discover_extension_paths,
    invalidate_extension_bytecode,
    load_extension,
    load_module,
    register_extension_entrypoint_key,
)
from vtx.plugins.services import ToolService

_NOOP = "def register(api):\n    pass\n"

_TOOL_EXTENSION = """
    def register(api):
        api.register_tool(
            "discovery_probe",
            "A tool the discovery tests register",
            {"type": "object", "properties": {}},
            execute=lambda args, ctx: {"success": True, "result": "probed"},
        )
"""


def _write(path: Path, source: str = _NOOP) -> Path:
    """Write an extension module, creating the directory tree above it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(source).strip() + "\n", encoding="utf-8")
    return path


def _rewrite_undetectable(path: Path, source: str) -> None:
    """Rewrite a file keeping its length and mtime.

    This is the edit a ``__pycache__`` check cannot see: same byte length, same
    mtime tick. Asserting the precondition here keeps a formatting change from
    quietly turning the staleness tests into ordinary reload tests.
    """
    before = path.stat()
    payload = source.encode("utf-8")
    assert len(payload) == before.st_size, "the rewrite must keep the file the same length"
    path.write_bytes(payload)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


@pytest.fixture(autouse=True)
def purge_extension_modules():
    """Keep one test's imported extension modules out of the next test's import.

    ``load_module`` derives a module's name from the *file*, not from the path,
    so two tests that both load ``extensions/plugin.py`` share one
    ``sys.modules`` entry and the second one silently binds to the first's
    submodule objects.
    """
    yield
    for name in [n for n in sys.modules if n.startswith("vtx_ext_")]:
        del sys.modules[name]


@pytest.fixture()
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point both the profile loader and discovery at a throwaway config dir.

    ``discover_extension_paths`` resolves the *agent* root through
    ``get_config_dir``, so a test that auto-discovers without this fixture would
    read — and an extension could write into — the developer's real home.
    """
    root = tmp_path / "config"
    (root / "agent" / "extensions").mkdir(parents=True)
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: root)
    monkeypatch.setattr("vtx.plugins.extensions.get_config_dir", lambda: root)
    return root


def _host_with_tools() -> PluginHost:
    """A minimal composition: just enough for an extension row to mount."""
    host = PluginHost([], label="discovery-test")
    host.add(plugin(name="tools")(lambda ctx, cfg: ctx.provide("tools", ToolService())))
    host.mount()
    return host


# --------------------------------------------------------------------------
# discover_extension_paths
# --------------------------------------------------------------------------


def test_discovery_is_project_then_agent_dir_then_configured(tmp_path: Path):
    """The order is the extension system's order, and it is load-bearing: a
    project-local file has to be able to shadow a global one of the same name."""
    project = tmp_path / "project"
    _write(project / ".vtx" / "extensions" / "local.py")

    agent_dir = tmp_path / "agent"
    global_file = _write(agent_dir / "extensions" / "global_ext.py")

    configured = tmp_path / "configured"
    _write(configured / "loose.py")

    paths = discover_extension_paths(
        cwd=str(project), configured=[str(configured), str(global_file)], agent_dir=agent_dir
    )
    assert paths == [
        (project / ".vtx" / "extensions" / "local.py").resolve(),
        global_file.resolve(),
        (configured / "loose.py").resolve(),
    ]


def test_a_file_reached_through_two_roots_is_discovered_once(tmp_path: Path):
    """The same file named as the agent-dir extension and as an explicit
    ``--extension`` must not produce two rows fighting over one tool name."""
    agent_dir = tmp_path / "agent"
    shared = _write(agent_dir / "extensions" / "shared.py")

    paths = discover_extension_paths(
        cwd=str(tmp_path),
        configured=[str(shared), str(shared.parent), str(shared)],
        agent_dir=agent_dir,
    )
    assert paths == [shared.resolve()]


def test_a_configured_path_that_does_not_exist_is_skipped(tmp_path: Path):
    """A stale ``extensions:`` entry in config.yml must not break the ones
    that do exist."""
    present = _write(tmp_path / "present.py")

    paths = discover_extension_paths(
        cwd=str(tmp_path),
        configured=[str(tmp_path / "deleted.py"), str(tmp_path / "gone_dir"), str(present)],
        agent_dir=tmp_path / "agent",
    )
    assert paths == [present.resolve()]


def test_a_configured_package_contributes_its_init_file(tmp_path: Path):
    """``--extension path/to/pkg`` means the package, not every file inside
    it: importing the sibling modules as extensions too would double-register."""
    package = tmp_path / "my_package"
    init = _write(package / "__init__.py")
    _write(package / "helper.py")

    paths = discover_extension_paths(
        cwd=str(tmp_path), configured=[str(package)], agent_dir=tmp_path / "agent"
    )
    assert paths == [init.resolve()]


def test_a_configured_directory_that_is_not_a_package_expands_to_its_modules(tmp_path: Path):
    """The other half of the rule: a directory with no ``__init__.py`` is a
    directory *of* extensions, so each module in it is its own row."""
    directory = tmp_path / "many"
    _write(directory / "one.py")
    _write(directory / "two.py")

    paths = discover_extension_paths(
        cwd=str(tmp_path), configured=[str(directory)], agent_dir=tmp_path / "agent"
    )
    assert [p.name for p in paths] == ["one.py", "two.py"]


def test_a_package_inside_a_discovery_directory_is_contributed_as_a_unit(tmp_path: Path):
    """A subdirectory of a discovery root contributes its ``__init__.py`` and
    nothing else, and dot/underscore-prefixed modules stay out of the way."""
    project = tmp_path / "project"
    root = project / ".vtx" / "extensions"
    _write(root / "visible.py")
    _write(root / "_private.py")
    _write(root / ".hidden.py")
    package_init = _write(root / "nested" / "__init__.py")
    _write(root / "nested" / "impl.py")

    paths = discover_extension_paths(
        cwd=str(project), configured=None, agent_dir=tmp_path / "agent"
    )
    # Sorted by entry name, so the nested package precedes ``visible.py``.
    assert paths == [package_init.resolve(), (root / "visible.py").resolve()]


def test_the_agent_dir_is_searched_when_a_project_has_none(tmp_path: Path, isolated_config_dir):
    """The global row is a real discovery place, not a fallback that only fires
    when nothing else was found."""
    _write(isolated_config_dir / "agent" / "extensions" / "global_only.py")

    paths = discover_extension_paths(cwd=str(tmp_path), configured=None)
    assert [p.name for p in paths] == ["global_only.py"]


# --------------------------------------------------------------------------
# load_module
# --------------------------------------------------------------------------


def test_load_module_imports_a_single_file(tmp_path: Path):
    module = load_module(_write(tmp_path / "solo.py", "VERSION = 'solo'\n"), "solo")
    assert module.VERSION == "solo"
    assert module.__name__ == "vtx_ext_solo"


def test_load_module_imports_a_package_with_submodule_search_locations(tmp_path: Path):
    """A package extension is loaded from its ``__init__.py`` but must keep a
    ``__path__``, or its own ``from .impl import ...`` cannot resolve."""
    package = tmp_path / "probepkg"
    _write(package / "impl.py", "VERSION = 'p1'\n")
    _write(package / "__init__.py", "from .impl import VERSION\n")

    module = load_module(package / "__init__.py")
    assert module.VERSION == "p1"
    assert list(module.__path__) == [str(package)]


def test_a_same_length_edit_in_the_same_mtime_tick_is_recompiled(tmp_path: Path):
    """The stale-bytecode hazard, asserted against the behaviour itself.

    A ``.pyc`` written for ``v1`` is left on disk, the source is rewritten to
    ``v2`` with the same length and the same mtime, and the ordinary loader
    (checked here as the control) still hands back ``v1``. ``load_module``
    compiles the text it just read, so it sees ``v2``.
    """
    path = _write(tmp_path / "versioned.py", 'VERSION = "v1"\n')
    py_compile.compile(str(path), doraise=True)
    assert list((tmp_path / "__pycache__").glob("versioned*.pyc")), (
        "the cache must exist to be stale"
    )

    _rewrite_undetectable(path, 'VERSION = "v2"\n')

    spec = importlib.util.spec_from_file_location("naive_control", str(path))
    control = importlib.util.module_from_spec(spec)
    sys.modules["naive_control"] = control
    try:
        spec.loader.exec_module(control)
        assert control.VERSION == "v1", "the cache is stale, which is the whole point"
    finally:
        sys.modules.pop("naive_control", None)

    assert load_module(path, "versioned").VERSION == "v2"


def test_a_package_submodule_edit_is_stale_until_the_bytecode_cache_is_dropped(tmp_path: Path):
    """Submodules go through the normal finder, so they need more than a fresh
    compile of the entry module: the package's ``__pycache__`` has to go too."""
    package = tmp_path / "stale_submodule_pkg"
    _write(package / "impl.py", "VERSION = 'p1'\n")
    _write(package / "__init__.py", "from .impl import VERSION\n")

    assert load_module(package / "__init__.py").VERSION == "p1"
    assert list((package / "__pycache__").glob("impl*.pyc")), (
        "the submodule must have been cached for this to be a staleness test"
    )
    _rewrite_undetectable(package / "impl.py", "VERSION = 'p2'\n")

    _forget_package_modules(package.name)
    assert load_module(package / "__init__.py").VERSION == "p1", "stale without invalidation"

    _rewrite_undetectable(package / "impl.py", "VERSION = 'p3'\n")
    _forget_package_modules(package.name)
    invalidate_extension_bytecode(package / "__init__.py")
    assert load_module(package / "__init__.py").VERSION == "p3"


def _forget_package_modules(package_name: str) -> None:
    """Drop the package from ``sys.modules`` the way a reload has to.

    ``invalidate_extension_bytecode`` only clears the bytecode cache; the finder
    short-circuits on an already-imported module, so the reload path purges both.
    """
    for name in [
        n
        for n in sys.modules
        if n == f"vtx_ext_{package_name}" or n.startswith(f"vtx_ext_{package_name}.")
    ]:
        del sys.modules[name]
    importlib.invalidate_caches()


def test_invalidate_extension_bytecode_only_removes_a_packages_cache(tmp_path: Path):
    """A single-module extension is compiled from source, so deleting a
    directory next to it would destroy files it never created."""
    single = _write(tmp_path / "solo.py", "VERSION = 'v1'\n")
    sentinel = tmp_path / "__pycache__" / "someone_elses.pyc"
    sentinel.parent.mkdir()
    sentinel.write_bytes(b"not ours")

    invalidate_extension_bytecode(single)
    assert sentinel.exists()

    package = tmp_path / "withpkg"
    _write(package / "__init__.py", "def register(api):\n    pass\n")
    cached = package / "__pycache__"
    cached.mkdir()
    (cached / "impl.cpython-313.pyc").write_bytes(b"stale")

    invalidate_extension_bytecode(package / "__init__.py")
    assert not cached.exists()


# --------------------------------------------------------------------------
# ExtensionLoadError
# --------------------------------------------------------------------------


def test_a_syntax_error_is_reported_against_the_path(tmp_path: Path):
    """A dozen extensions loading at once is why the message leads with the
    path; and the failed module must not stay in ``sys.modules`` waiting for a
    later reload to bind to it."""
    path = _write(tmp_path / "broken.py", "def register(api)\n    pass\n")

    with pytest.raises(ExtensionLoadError) as caught:
        load_module(path, "broken")

    error = caught.value
    assert "failed to import" in str(error)
    assert str(path) in str(error)
    assert isinstance(error.error, SyntaxError)
    assert "vtx_ext_broken" not in sys.modules


def test_a_module_without_register_is_reported_as_not_exporting_one(tmp_path: Path):
    """A module that is not an extension still has to fail with an
    attributable message rather than an ``AttributeError`` from somewhere
    deeper in the loader."""
    path = _write(tmp_path / "no_register.py", "def something_else(api):\n    pass\n")

    with pytest.raises(ExtensionLoadError) as caught:
        load_extension(path, cwd=str(tmp_path))

    assert "does not export" in str(caught.value)
    assert str(path) in str(caught.value)
    assert caught.value.error == "does not export a top-level `register(api)` function"


# --------------------------------------------------------------------------
# load_extension
# --------------------------------------------------------------------------


def test_load_extension_runs_register_and_returns_the_record_it_filled(tmp_path: Path):
    """One module loaded without a composition still has to produce the record
    ``/extensions`` reads: the registrations ran, and the names are keyed the
    way the runtime looks them up."""
    path = _write(
        tmp_path / "greeter.py",
        """
        def register(api):
            api.register_tool(
                "greet",
                "Greet someone",
                {"type": "object", "properties": {"name": {"type": "string"}}},
                execute=lambda args, ctx: {"success": True, "result": f"hi {args['name']}"},
            )
            api.register_command("hello", "say hi", lambda args: f"hi {args or 'world'}")
        """,
    )

    record = load_extension(path, cwd=str(tmp_path))

    assert record.name == "greeter"
    assert sorted(record.tools) == ["greet"]
    assert sorted(record.commands) == ["hello"]
    assert record.describe()["tools"] == ["greet"]
    assert record.commands["hello"].handler("").output == "hi world"


def test_load_extension_reports_a_register_that_raises(tmp_path: Path):
    """``register`` is the extension's own code, so its failure is reported
    with the message the author wrote, not swallowed as a silent no-op."""
    path = _write(tmp_path / "explodes.py", "def register(api):\n    raise RuntimeError('boom')\n")

    with pytest.raises(ExtensionLoadError) as caught:
        load_extension(path, cwd=str(tmp_path))

    assert "register(api) raised" in str(caught.value)
    assert "boom" in str(caught.value)
    assert str(path) in str(caught.value)


# --------------------------------------------------------------------------
# ExtensionService
# --------------------------------------------------------------------------


def test_the_service_returns_one_plugin_per_discovered_file(tmp_path: Path):
    """Discovery composes *before* mount: one row per file, named for the
    module, carrying the path it came from so ``/extensions`` can name it."""
    project = tmp_path / "project"
    first = _write(project / ".vtx" / "extensions" / "first.py")
    second = _write(project / ".vtx" / "extensions" / "second.py")

    service = ExtensionService()
    plugins = service.load(cwd=str(project), entry_points=False)

    assert [p.name for p in plugins] == ["extension:first", "extension:second"]
    assert [p.meta["path"] for p in plugins] == [str(first.resolve()), str(second.resolve())]


def test_auto_discover_false_keeps_only_the_callers_explicit_files(tmp_path: Path):
    """``auto_discover=False`` is how a caller says "these, and nothing else":
    the directory scan must not smuggle in rows nobody asked for."""
    project = tmp_path / "project"
    _write(project / ".vtx" / "extensions" / "autodiscovered.py")
    wanted = _write(tmp_path / "explicit" / "wanted.py")

    service = ExtensionService()
    plugins = service.load(
        cwd=str(project), auto_discover=False, extra_paths=[str(wanted)], entry_points=False
    )

    assert [p.name for p in plugins] == ["extension:wanted"]
    assert [p.meta["path"] for p in plugins] == [str(wanted.resolve())]


def test_auto_discover_false_with_a_directory_keeps_the_files_inside_it(tmp_path: Path):
    """What the previous test proves for a file, proven for a directory.

    ``--extension path/to/dir`` means the same thing to a user whichever way the
    caller spells it, so both spellings have to survive the auto-discovery
    filter. This used to fail: the filter compared each expanded file against
    the raw directory entry and dropped them all, so naming a directory silently
    loaded nothing.
    """
    directory = tmp_path / "explicit"
    _write(directory / "wanted.py")

    service = ExtensionService()
    plugins = service.load(
        cwd=str(tmp_path), auto_discover=False, extra_paths=[str(directory)], entry_points=False
    )

    assert [p.name for p in plugins] == ["extension:wanted"]


def test_a_failed_register_leaves_an_error_and_no_record(tmp_path: Path):
    """A half-built row is worse than none: the record is dropped by ``forget``
    and the error names the file, so ``/extensions`` shows one failure rather
    than an extension that registered half its tools.

    The stale record is seeded first because that is the real case — a reload,
    where the previous pass's record is still there when the new one fails.
    """
    good = _write(tmp_path / "good.py", _TOOL_EXTENSION)
    bad = _write(tmp_path / "bad.py", "def register(api):\n    raise RuntimeError('boom')\n")

    service = ExtensionService()
    stale = service.loaded.record("bad")

    plugins = service.load(
        cwd=str(tmp_path),
        auto_discover=False,
        extra_paths=[str(good), str(bad)],
        entry_points=False,
    )

    host = _host_with_tools()
    try:
        for spec in plugins:
            host.add(spec)
        host.mount()

        assert [r.name for r in service.loaded.extensions] == ["good"]
        assert stale not in service.loaded.extensions, "forget() must drop the failed record"
        assert len(service.loaded.errors) == 1
        assert str(bad.resolve()) in service.loaded.errors[0]
        assert "boom" in service.loaded.errors[0]
    finally:
        host.dispose()


def test_a_successful_row_publishes_its_tools_into_the_composition(tmp_path: Path):
    """``inject=("tools",)`` plus ``mount_tool`` is what makes an extension's
    tool reachable from ``ctx.tools`` — and what makes unmounting the row take
    the tool back out again."""
    path = _write(tmp_path / "probes.py", _TOOL_EXTENSION)

    service = ExtensionService()
    plugins = service.load(
        cwd=str(tmp_path), auto_discover=False, extra_paths=[str(path)], entry_points=False
    )

    host = _host_with_tools()
    try:
        for spec in plugins:
            host.add(spec)
        host.mount()

        assert host.base.tools.get("discovery_probe") is not None
        assert service.loaded.describe()[0]["tools"] == ["discovery_probe"]

        assert host.unmount("extension:probes") is True
        assert host.base.tools.get("discovery_probe") is None
    finally:
        host.dispose()


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


class _FakeEntryPoint:
    def __init__(self, value: str) -> None:
        self.value = value


def test_discover_entrypoints_is_a_list_when_nothing_is_installed(monkeypatch: pytest.MonkeyPatch):
    """No package declares ``vtx.extensions``, which is the normal case; the
    result has to be an empty list a caller can iterate, not an error."""
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda *, group: [])

    assert discover_entrypoints() == []


def test_a_registered_entrypoint_group_is_searched(monkeypatch: pytest.MonkeyPatch):
    """An embedder's own packages need a group name they control, and the
    registration has to be visible in the search order that follows."""
    monkeypatch.setattr(sys, _ENTRYPOINT_KEYS_ATTR, [], raising=False)
    register_extension_entrypoint_key("vtx.acme.extensions")
    assert "vtx.acme.extensions" in _entrypoint_keys()

    searched: list[str] = []

    def fake_entry_points(*, group: str):
        searched.append(group)
        if group == "vtx.acme.extensions":
            return [_FakeEntryPoint("acme.contrib:register")]
        return []

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)

    assert discover_entrypoints() == ["acme.contrib:register"]
    assert searched == [*ENTRYPOINT_KEYS, "vtx.acme.extensions"]


def test_a_broken_metadata_backend_does_not_break_discovery(monkeypatch: pytest.MonkeyPatch):
    """Installed-package metadata is best-effort: a distribution whose metadata
    will not parse must not stop the file-based rows from being discovered."""
    monkeypatch.setattr(sys, _ENTRYPOINT_KEYS_ATTR, [], raising=False)

    def exploding_entry_points(*, group: str):
        raise RuntimeError("corrupt dist-info")

    monkeypatch.setattr(importlib.metadata, "entry_points", exploding_entry_points)
    assert discover_entrypoints() == []


# --------------------------------------------------------------------------
# End to end: discovery through boot
# --------------------------------------------------------------------------


@pytest.fixture()
def booted(tmp_path: Path, isolated_config_dir: Path):
    """Boot a composition rooted at ``tmp_path``, and leave the next test clean.

    ``boot`` writes a module global, so a test that boots without resetting it
    hands its mounted rows to whatever runs next.
    """
    hosts: list[PluginHost] = []

    def _boot(**kwargs) -> PluginHost:
        host = boot(cwd=str(tmp_path), **kwargs)
        hosts.append(host)
        return host

    yield _boot
    reset()
    for host in reversed(hosts):
        host.dispose()


def test_a_discovered_extension_mounts_through_boot_and_unmounts_cleanly(tmp_path, booted):
    """The property a user depends on: an extension file under
    ``.vtx/extensions/`` becomes a live tool with no wiring of its own, and
    unloading its row really removes it again."""
    _write(tmp_path / ".vtx" / "extensions" / "booted.py", _TOOL_EXTENSION)

    host = booted()
    try:
        assert host.base.extensions.loaded.errors == []
        assert "extension:booted" in host.mounted_ids()
        assert host.base.tools.get("discovery_probe") is not None

        assert host.unmount("extension:booted") is True
        assert host.base.tools.get("discovery_probe") is None
    finally:
        host.dispose()


def test_a_discovered_records_path_is_the_file_it_came_from(tmp_path, booted):
    """``/extensions`` and ``--list-extensions`` print ``record.path``.

    A discovered row used to record ``Path(module_name)``, so what a user saw
    was the bare stem — ``booted`` rather than the file they wrote. The path is
    the one thing that tells them which of several same-named modules is live.
    """
    written = _write(tmp_path / ".vtx" / "extensions" / "booted.py", _TOOL_EXTENSION)

    host = booted()
    try:
        record = host.base.extensions.loaded.record("booted")
        assert record.path == written.resolve()
    finally:
        host.dispose()


def test_an_explicit_extension_path_mounts_through_boot(tmp_path: Path, booted):
    """``--extension PATH`` reaches ``boot`` as an explicit path; the row it
    creates is an ordinary row, so it is addressable by the same id."""
    _write(tmp_path / "elsewhere" / "passed.py", _TOOL_EXTENSION)

    host = booted(
        auto_discover_extensions=False, extension_paths=[str(tmp_path / "elsewhere" / "passed.py")]
    )
    try:
        assert host.base.extensions.loaded.errors == []
        assert "extension:passed" in host.mounted_ids()
        assert host.base.tools.get("discovery_probe") is not None
    finally:
        host.dispose()
