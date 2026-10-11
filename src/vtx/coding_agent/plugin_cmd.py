"""The ``vtx plugin`` command surface.

The profile is a directory with two files: ``bundle.txt`` (the ordered bundle
list) and ``cordis.yml`` (the user's patch layer). Both are plain text on
purpose — the row list is the product, so it should be readable and diffable
without running vtx.

``enable`` and ``disable`` rewrite only the ``disabled`` flag of the matching
row in ``cordis.yml``, appending an override when there is none yet. Nothing else
in the file is touched.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from vtx.plugins.builtins import BUNDLES, PROFILE_TEMPLATES
from vtx.plugins.errors import PatchError
from vtx.plugins.loader import RowLoader, load_yaml
from vtx.plugins.profile import (
    DEFAULT_PROFILE,
    init_profile,
    profile_dir,
    profile_file,
    read_bundles,
    rows_for_bundles,
)


def _fail(message: str) -> int:
    print(f"vtx plugin: {message}", file=sys.stderr)
    return 1


def _print_profiles() -> int:
    print("profiles:")
    for name, bundles in PROFILE_TEMPLATES.items():
        print(f"  {name}\t{', '.join(bundles)}")
    print("bundles:")
    for name, rows in BUNDLES.items():
        print(f"  {name}\t{len(rows)} rows")
    print(f"\ndefault profile: {DEFAULT_PROFILE}")
    return 0


def _print_path(name: str) -> int:
    print(str(profile_dir(name)))
    return 0


def _read_patch(path: Path) -> Any:
    if not path.exists():
        return []
    return load_yaml(path.read_text(encoding="utf-8")) or []


def _row_ids(entries: list[Any]) -> list[str]:
    ids: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            value = entry.get("id")
            if isinstance(value, str):
                ids.append(value)
            for op in ("insert", "set", "replace", "override"):
                for row in entry.get(op, []) or []:
                    if isinstance(row, dict) and isinstance(row.get("id"), str):
                        ids.append(row["id"])
        elif hasattr(entry, "id"):
            # A Row dataclass from a bundle.
            ids.append(str(entry.id))
    return ids


def _toggle(name: str, row_id: str, *, disabled: bool) -> int:
    """Set ``row_id``'s disabled flag in the profile's own patch layer."""
    path = profile_file(name)
    data = _read_patch(path)
    if not isinstance(data, list):
        return _fail(f"{path}: expected a list of patch operations")
    known = _row_ids(data)
    if row_id not in known:
        # Drop any prior override so the row keeps exactly one home in this file.
        if row_id not in _row_ids(rows_for_bundles(read_bundles(name))):
            return _fail(f"unknown row {row_id!r}; run `vtx --dump-config` to list them")
        data.append({"override": [{"id": row_id, "disabled": disabled}]})
    else:
        data = _apply_toggle(data, row_id, disabled)
    header = (
        "# Your patch layer for this vtx profile, applied after every bundle layer.\n"
        "# Rows are addressed by id; `disabled: true` keeps the row installed but inactive.\n"
    )
    body = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{header}{body}", encoding="utf-8")
    print(f"vtx plugin: {row_id} {'disabled' if disabled else 'enabled'} in profile {name!r}")
    return 0


def _apply_toggle(data: list[Any], row_id: str, disabled: bool) -> list[Any]:
    for entry in data:
        if not isinstance(entry, dict):
            continue
        for op in ("insert", "set", "replace", "override"):
            rows = entry.get(op)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict) and row.get("id") == row_id:
                    if disabled:
                        row["disabled"] = True
                    else:
                        row.pop("disabled", None)
                    return data
        if entry.get("id") == row_id:
            if disabled:
                entry["disabled"] = True
            else:
                entry.pop("disabled", None)
            return data
    return data


def _install(profile: str, rest: list[str]) -> int:
    from vtx.plugins.install import install, install_from_git

    upgrade = "--upgrade" in rest
    specs = [item for item in rest if not item.startswith("-")]
    if not specs:
        return _fail("install needs a package name or git URL")
    spec = specs[0]
    if spec.startswith(("http://", "https://", "git@", "ssh://", "git://")):
        ok, message = install_from_git(spec, profile=profile)
    else:
        ok, message = install(spec, profile=profile, upgrade=upgrade)
    if ok:
        print(f"vtx plugin: {message}")
        print(
            "vtx plugin: add a row for it in ~/.vtx/profiles/"
            f"{profile}/cordis.yml with `- insert:`"
        )
        return 0
    return _fail(message)


def _exemption(profile: str, command: str, rest: list[str]) -> int:
    from vtx.core.version import VERSION
    from vtx.plugins.install import write_exemption

    granted = command == "allow-version"
    target = next((item for item in rest if not item.startswith("-")), None)
    version = None
    iterator = iter(rest)
    for item in iterator:
        if item == "--vtx-version":
            version = next(iterator, None)
        elif item.startswith("--vtx-version="):
            version = item.split("=", 1)[1]
    if target is None or version is None:
        return _fail(f"usage: vtx plugin {command} <package@version> --vtx-version <exact>")
    if "@" not in target:
        return _fail(f"{target!r} must be package@version, not just a package name")
    if granted:
        sys.stderr.write(
            "vtx plugin: warning: allowing an incompatible plugin can break the application "
            "or corrupt data. The grant applies only to this exact pair.\n"
        )
    try:
        write_exemption(
            profile, target, version, enabled=granted, accept_risk="--accept-risk" in rest
        )
    except PatchError as exc:
        return _fail(str(exc))
    verb = "allowed" if granted else "revoked"
    print(f"vtx plugin: {verb} {target} for vtx {version}")
    if granted and version != VERSION:
        print(
            f"vtx plugin: note: this profile runs vtx {VERSION}, so the grant is not "
            f"in effect until it does"
        )
    return 0


def _list_exemptions(profile: str) -> int:
    import json

    from vtx.plugins.install import read_exemptions

    grants, warnings = read_exemptions(profile)
    for warning in warnings:
        sys.stderr.write(f"vtx plugin: warning: {warning}\n")
    print(json.dumps(grants, indent=2))
    return 0


def run_plugin_command(argv: list[str]) -> int:
    """Dispatch ``vtx plugin ...``. Returns a process exit code."""
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__ or "")
        print("commands: profiles | init NAME | path NAME | enable ROW | disable ROW")
        return 0
    # ``--profile`` may appear anywhere; it selects which patch file is edited.
    profile = DEFAULT_PROFILE
    if "--profile" in argv:
        index = argv.index("--profile")
        if index + 1 >= len(argv):
            return _fail("--profile needs a name")
        profile = argv[index + 1]
        argv = [*argv[:index], *argv[index + 2 :]]
    command, rest = argv[0], argv[1:]
    try:
        if command == "profiles":
            return _print_profiles()
        if command == "init":
            if not rest:
                return _fail("init needs a profile name")
            directory = init_profile(rest[0])
            print(f"vtx plugin: initialised {directory}")
            return 0
        if command == "path":
            if not rest:
                return _fail("path needs a profile name")
            return _print_path(rest[0])
        if command == "enable":
            if not rest:
                return _fail("enable needs a row id")
            return _toggle(profile, rest[0], disabled=False)
        if command == "disable":
            if not rest:
                return _fail("disable needs a row id")
            return _toggle(profile, rest[0], disabled=True)
        if command == "install":
            if not rest:
                return _fail("install needs a package name or a git URL")
            return _install(profile, rest)
        if command in ("allow-version", "revoke-version"):
            return _exemption(profile, command, rest)
        if command == "version-exemptions":
            return _list_exemptions(profile)
    except PatchError as exc:
        return _fail(str(exc))
    return _fail(f"unknown plugin command {command!r}")


__all__ = ["RowLoader", "run_plugin_command"]
