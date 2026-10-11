"""Installing plugins from PyPI or git, with a version-compatibility gate.

A plugin declares the runtime versions it was built against in its
``pyproject.toml``::

    [project.optional-dependencies]
    vtx = [">=1.2,<2.0"]

An incompatible one refuses to install before anything is downloaded or any
build script runs, and the refusal prints the exact command that grants an
exemption. ``allow-version`` records an exact ``package@version`` against an
exact ``vtx`` version in the profile's ``compatibility.json``; it grants nothing
beyond that pair, and neither a plugin upgrade nor a vtx upgrade inherits it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vtx.core.version import VERSION
from vtx.plugins.errors import PatchError
from vtx.plugins.profile import DEFAULT_PROFILE, profile_dir

COMPATIBILITY_FILENAME = "compatibility.json"


@dataclass(frozen=True)
class InstallPlan:
    """What the gate decided before anything was downloaded."""

    package: str
    version: str
    spec: str
    required_range: str | None
    compatible: bool
    reason: str = ""

    @property
    def exempt_key(self) -> str:
        return f"{self.package.split('==')[0]}@{self.version}"


def _profile_compat_path(profile: str) -> Path:
    return profile_dir(profile) / COMPATIBILITY_FILENAME


def read_exemptions(profile: str) -> tuple[dict[str, list[str]], list[str]]:
    """The profile's grants, plus any diagnostic lines worth surfacing."""
    path = _profile_compat_path(profile)
    if not path.exists():
        return {}, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {}, [f"{path.name} is unreadable and was ignored: {exc}"]
    raw = data.get("exemptions", {})
    if not isinstance(raw, dict):
        return {}, [f"{path.name}: 'exemptions' must be a mapping"]
    grants: dict[str, list[str]] = {}
    for key, value in raw.items():
        if isinstance(value, list):
            grants[key] = [str(v) for v in value]
    return grants, []


def write_exemption(
    profile: str, key: str, runtime_version: str, *, enabled: bool, accept_risk: bool = False
) -> None:
    """Grant or revoke an exact ``package@version`` for an exact vtx version."""
    if enabled and not accept_risk:
        raise PatchError(
            f"granting {key} for vtx {runtime_version} requires --accept-risk: an "
            "incompatible plugin can crash the application or corrupt session data"
        )
    grants, _warnings = read_exemptions(profile)
    versions = set(grants.get(key, []))
    if enabled:
        versions.add(runtime_version)
    else:
        versions.discard(runtime_version)
        if not versions:
            grants.pop(key, None)
    if versions:
        grants[key] = sorted(versions)
    path = _profile_compat_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"exemptions": grants}, indent=2) + "\n", encoding="utf-8")


#: Requirement groups whose entries are constraints on vtx itself.
VTX_GROUPS = frozenset({"vtx", "vtx-plugin", "plugins"})
#: The distribution names that mean "this runtime".
VTX_PACKAGES = ("vtx-coding-agent", "vtx")


def _package_of(spec: str) -> str:
    """The distribution name a spec names, dropping any ``==version`` or path."""
    text = spec.strip().rstrip("/")
    text = text.removesuffix(".git")
    if "==" in text:
        text = text.split("==", 1)[0]
    if text.startswith((".", "/", "~")) or (len(text) > 2 and text[1] == ":"):
        return Path(text).name
    return text.split("@")[-1].rsplit("/", 1)[-1]


def _declared_range(pyproject: dict[str, Any]) -> str | None:
    """The ``vtx`` requirement a package declares, if any.

    Both spellings are accepted, because both are in the wild::

        [project.optional-dependencies]
        vtx = [">=1.2,<2.0"]                      # the group *is* the requirement
        vtx = ["vtx-coding-agent>=1.2,<2.0"]       # the entry names the package
    """
    optional = pyproject.get("project", {}).get("optional-dependencies", {})
    for group, entries in optional.items():
        if group not in VTX_GROUPS or not isinstance(entries, list):
            continue
        for entry in entries:
            text = str(entry).strip()
            lowered = text.lower()
            for package in VTX_PACKAGES:
                if lowered.startswith(package):
                    return text[len(package) :].strip() or "any"
            # A bare specifier inside a group already named for vtx.
            if text and text[0] in "<>=!~":
                return text
    return None


def _satisfies(version: str, requirement: str) -> bool:
    """Whether ``version`` satisfies ``requirement``.

    Accepts both a bare specifier (``>=1.2,<2.0``) and one that names the
    package (``vtx-coding-agent>=1.2,<2.0``). A runtime version that is not
    PEP 440 — an editable checkout reports ``"editable"`` — cannot be compared,
    so it is treated as satisfied rather than crashing the gate; see
    :func:`comparable`.
    """
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version

    try:
        release = Version(version)
    except InvalidVersion:
        return True
    try:
        return release in SpecifierSet(requirement)
    except InvalidSpecifier:
        pass
    try:
        return release in Requirement(requirement).specifier
    except InvalidRequirement:
        return True


def comparable(version: str = VERSION) -> bool:
    """Whether ``version`` can be ordered against a requirement at all."""
    from packaging.version import InvalidVersion, Version

    try:
        Version(version)
    except InvalidVersion:
        return False
    return True


def plan_install(spec: str, *, profile: str = DEFAULT_PROFILE) -> InstallPlan:
    """Resolve ``spec`` and decide whether it may be installed.

    A local path is read from its own ``pyproject.toml``. A git URL or tarball
    cannot be judged without fetching, so it is judged after installation (see
    :func:`verify_installed`).
    """
    if spec.startswith((".", "/", "~")) or (len(spec) > 5 and spec[1] == ":"):
        path = Path(spec).expanduser()
        if not (path / "pyproject.toml").exists():
            return InstallPlan(_package_of(spec), "", spec, None, False, "no pyproject.toml")
        try:
            data = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            return InstallPlan(_package_of(spec), "", spec, None, False, f"unreadable: {exc}")
        required = _declared_range(data)
        version = str(data.get("project", {}).get("version", "0.0.0"))
        # The distribution name, not the path: an exemption keys on package@version.
        name = str(data.get("project", {}).get("name") or _package_of(spec))
        if required is None:
            return InstallPlan(name, version, str(path), None, True, "declares no vtx requirement")
        ok = _satisfies(VERSION, required)
        return InstallPlan(
            name,
            version,
            str(path),
            required,
            ok,
            "" if ok else f"requires vtx {required}, running {VERSION}",
        )
    return InstallPlan(spec, "", spec, None, True, "unjudged until installed")


def verify_installed(package: str, *, profile: str = DEFAULT_PROFILE) -> InstallPlan:
    """Re-check a package that is now on disk, for specs judged after install."""
    import importlib.metadata

    try:
        distribution = importlib.metadata.distribution(package)
    except importlib.metadata.PackageNotFoundError:
        return InstallPlan(package, "", package, None, False, "not installed")
    requires = distribution.requires or []
    for entry in requires:
        name = entry.split(";")[0].split()[0] if entry.split() else ""
        if name.replace("_", "-") not in ("vtx-coding-agent", "vtx"):
            continue
        requirement = entry.split(";")[0]
        ok = _satisfies(VERSION, requirement)
        return InstallPlan(
            package,
            distribution.version,
            package,
            requirement,
            ok,
            "" if ok else f"requires {requirement}, running vtx {VERSION}",
        )
    return InstallPlan(
        package, distribution.version, package, None, True, "declares no vtx requirement"
    )


def is_exempt(plan: InstallPlan, *, profile: str = DEFAULT_PROFILE) -> bool:
    if plan.compatible:
        return True
    grants, _warnings = read_exemptions(profile)
    return VERSION in grants.get(plan.exempt_key, [])


def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=600, **kwargs)


def install(
    spec: str, *, profile: str = DEFAULT_PROFILE, upgrade: bool = False
) -> tuple[bool, str]:
    """Install a plugin package, refusing an incompatible one before it runs."""
    plan = plan_install(spec, profile=profile)
    if not plan.compatible and not is_exempt(plan, profile=profile):
        return False, (
            f"{plan.package} is incompatible: {plan.reason}\n"
            f"to install it anyway, grant an exemption for exactly this pair:\n"
            f"  vtx plugin --profile {profile} allow-version {plan.exempt_key} "
            f"--vtx-version {VERSION} --accept-risk"
        )

    command = [sys.executable, "-m", "uv", "pip", "install"]
    if upgrade:
        command.append("--upgrade")
    command.append(plan.spec)
    result = _run(command)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "install failed").strip()

    # A git or tarball spec could only be judged once fetched.
    if plan.reason == "unjudged until installed":
        package = _package_of(spec)
        verified = verify_installed(package, profile=profile)
        if not verified.compatible and not is_exempt(verified, profile=profile):
            restore = _run([sys.executable, "-m", "uv", "pip", "uninstall", "-y", package])
            detail = "" if restore.returncode == 0 else " (and the rollback also failed)"
            return False, (
                f"{package} is incompatible and was removed{detail}: {verified.reason}\n"
                f"  vtx plugin --profile {profile} allow-version {verified.exempt_key} "
                f"--vtx-version {VERSION} --accept-risk"
            )
    return True, f"installed {plan.spec}"


def install_from_git(url: str, *, profile: str = DEFAULT_PROFILE) -> tuple[bool, str]:
    """Clone and install a plugin repository."""
    repo = _package_of(url).lower().replace("-", "_")
    with tempfile.TemporaryDirectory(prefix="vtx-plugin-") as tmp:
        result = _run(["git", "clone", "--depth", "1", url, str(Path(tmp) / repo)])
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "git clone failed").strip()
        return install(str(Path(tmp) / repo), profile=profile)


__all__ = [
    "COMPATIBILITY_FILENAME",
    "InstallPlan",
    "install",
    "install_from_git",
    "is_exempt",
    "plan_install",
    "read_exemptions",
    "verify_installed",
    "write_exemption",
]
