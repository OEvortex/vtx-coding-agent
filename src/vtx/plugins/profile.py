"""Profiles, bundles, and the composed row list a harness boots from.

A **profile** is a named composition in the vtx home. It lists the bundles it
stacks and carries the user's own patch layer. A **bundle** is a named set of
rows that ships inside a package; a row a bundle inserts stays patchable by every
layer above it.

Layers apply in order onto an empty list, and the last write for a row id wins:

    profile's ordered bundles  ->  profile patch  ->  ``--patch`` overlays

A row's ``config`` is a whole value rather than a merge. That is deliberate: a
row whose value differs by mode lives in the mode's own layer, so every row is
owned by exactly one bundle plus the user's patch, and a later layer never has to
know what it is overwriting.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from vtx.core import paths as _paths
from vtx.plugins.builtins import BUNDLES, DEFAULT_PROFILE_BUNDLES, PROFILE_TEMPLATES
from vtx.plugins.errors import PatchError
from vtx.plugins.loader import Row, apply_patch, load_yaml_file

#: The profile used when none is named.
DEFAULT_PROFILE = "default"

PROFILES_DIRNAME = "profiles"
PROFILE_FILENAME = "cordis.yml"

PROFILE_PATCH_TEMPLATE = """\
# Your patch layer for this vtx profile, applied after every bundle layer.
# A list of operations addressed by row id; see vtx.plugins.loader.
#
#   - set:
#       - id: llm
#         disabled: true
#   - insert:
#       - id: my-thing
#         name: my_package.plugins:PLUGIN
#         config:
#           answer: 42
#   - remove:
#       - tool-grep
[]
"""


def profiles_dir() -> Path:
    """``~/.vtx/profiles`` — one directory per named profile."""
    return _paths.get_config_dir() / PROFILES_DIRNAME


def profile_dir(name: str = DEFAULT_PROFILE) -> Path:
    _validate_profile_name(name)
    return profiles_dir() / name


def profile_file(name: str = DEFAULT_PROFILE) -> Path:
    return profile_dir(name) / PROFILE_FILENAME


def _validate_profile_name(name: str) -> str:
    if not name or "/" in name or "\\" in name or name in (".", "..") or name.startswith("-"):
        raise PatchError(f"invalid profile name {name!r}")
    return name


def init_profile(name: str, bundles: Sequence[str] | None = None) -> Path:
    """Create a profile directory with its bundle list and an empty patch."""
    _validate_profile_name(name)
    directory = profile_dir(name)
    directory.mkdir(parents=True, exist_ok=True)
    package = directory / "bundle.txt"
    if bundles is None:
        bundles = PROFILE_TEMPLATES.get(name, DEFAULT_PROFILE_BUNDLES)
    package.write_text("\n".join(bundles) + "\n", encoding="utf-8")
    patch = directory / PROFILE_FILENAME
    if not patch.exists():
        patch.write_text(PROFILE_PATCH_TEMPLATE, encoding="utf-8")
    return directory


def read_bundles(name: str) -> list[str]:
    """The ordered bundle list a profile stacks."""
    _validate_profile_name(name)
    path = profile_dir(name) / "bundle.txt"
    if path.exists():
        names = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
        return [n for n in names if n and not n.startswith("#")]
    return list(PROFILE_TEMPLATES.get(name, DEFAULT_PROFILE_BUNDLES))


def rows_for_bundles(bundles: Sequence[str]) -> list[Row]:
    """Concatenate the rows of each named bundle, in order."""
    rows: list[Row] = []
    for bundle in bundles:
        if bundle not in BUNDLES:
            raise PatchError(
                f"unknown bundle {bundle!r}; known bundles: {', '.join(sorted(BUNDLES))}"
            )
        rows.extend(BUNDLES[bundle])
    return rows


def _profile_patch(name: str) -> Any:
    path = profile_file(name)
    if not path.exists():
        return None
    data = load_yaml_file(path)
    if data is None:
        return None
    if not isinstance(data, list):
        raise PatchError(f"{path}: the profile patch must be a YAML list")
    return data


def load_profile(
    name: str = DEFAULT_PROFILE, patches: Sequence[str | Path] | None = None
) -> list[Row]:
    """Compose a profile's full row list.

    ``patches`` are extra YAML files applied after the profile's own layer, in
    the order given.
    """
    rows = rows_for_bundles(read_bundles(name))
    rows = apply_patch(rows, _profile_patch(name), layer_name=f"profile:{name}")
    for patch_path in patches or ():
        data = load_yaml_file(patch_path)
        if data is None:
            continue
        if not isinstance(data, list):
            raise PatchError(f"{patch_path}: a patch layer must be a YAML list")
        rows = apply_patch(rows, data, layer_name=str(patch_path))
    return rows


def dump_config(
    name: str = DEFAULT_PROFILE, patches: Sequence[str | Path] | None = None
) -> list[dict[str, Any]]:
    """The composed row list as plain dicts, for ``--dump-config``."""
    return [row.to_dict() for row in load_profile(name, patches)]


def dump_config_json(
    name: str = DEFAULT_PROFILE, patches: Sequence[str | Path] | None = None
) -> str:
    return json.dumps(dump_config(name, patches), indent=2)


__all__ = [
    "DEFAULT_PROFILE",
    "PROFILES_DIRNAME",
    "PROFILE_FILENAME",
    "PROFILE_PATCH_TEMPLATE",
    "dump_config",
    "dump_config_json",
    "init_profile",
    "load_profile",
    "profile_dir",
    "profile_file",
    "profiles_dir",
    "read_bundles",
    "rows_for_bundles",
]
