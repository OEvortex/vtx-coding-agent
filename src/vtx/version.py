import json
import tomllib
from importlib import metadata
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _get_package_name() -> str:
    pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
    if pyproject_path.exists():
        try:
            data = tomllib.loads(pyproject_path.read_text())
            return data["project"]["name"]
        except Exception:
            pass
    return "vtx-coding-agent"


PACKAGE_NAME = _get_package_name()


def _is_editable_install() -> bool:
    try:
        dist = metadata.distribution(PACKAGE_NAME)
    except PackageNotFoundError:
        return False
    direct_url = dist.read_text("direct_url.json")
    if not direct_url:
        return False
    try:
        return json.loads(direct_url).get("dir_info", {}).get("editable", False)
    except json.JSONDecodeError:
        return False


if _is_editable_install():
    VERSION = "editable"  # Local editable build, not a released version
else:
    try:
        VERSION = version(PACKAGE_NAME)
    except PackageNotFoundError:
        VERSION = "0.2.5"  # Fallback version if package metadata is not available


def format_version() -> str:
    """Human-friendly version label for display (e.g. ``v0.2.3`` / ``v-editable``)."""
    return "v-editable" if VERSION == "editable" else f"v{VERSION}"
