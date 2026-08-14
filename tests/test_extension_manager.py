"""Tests for the extension manager (vtx install/uninstall/list)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from vtx.extension_manager import (
    InstalledExtension,
    _discover_entry_points,
    _discover_package_layout,
    _load_installed,
    _save_installed,
    get_installed_extension,
    list_installed,
)


def test_installed_extensions_default_empty():
    """When no installed file exists, list_installed returns []."""
    with patch("vtx.extension_manager._get_installed_path") as mock_path:
        mock_path.return_value = Path("/nonexistent/does-not-exist.yml")
        assert list_installed() == []


def test_save_and_load_installed(tmp_path: Path):
    """Round-trip save/load of installed extensions."""
    fake_path = tmp_path / "installed.yml"

    with patch("vtx.extension_manager._get_installed_path", return_value=fake_path):
        _save_installed(
            {
                "crs": InstalledExtension(
                    name="crs",
                    source="vtx-crs",
                    version="0.1.0",
                    extensions=["vtx_crs.ext:register"],
                    agents=["vtx_crs.agent:AGENT"],
                )
            }
        )
        loaded = _load_installed()
        assert "crs" in loaded
        assert loaded["crs"].source == "vtx-crs"
        assert loaded["crs"].version == "0.1.0"
        assert loaded["crs"].extensions == ["vtx_crs.ext:register"]
        assert loaded["crs"].agents == ["vtx_crs.agent:AGENT"]


def test_get_installed_extension_missing():
    with patch("vtx.extension_manager._load_installed", return_value={}):
        assert get_installed_extension("nonexistent") is None


def test_discover_entry_points(tmp_path: Path):
    """Entry point discovery reads pyproject.toml."""
    pyproject = tmp_path / "pyproject.toml"
    entry_points = (
        '[project.entry-points."vtx.extensions"]\n'
        'crs = "vtx_crs.ext:register"\n\n'
        '[project.entry-points."vtx.agents"]\n'
        'crs = "vtx_crs.agent:AGENT"\n'
    )
    pyproject.write_text(entry_points, encoding="utf-8")
    extensions, agents = _discover_entry_points(tmp_path)
    assert extensions == ["vtx_crs.ext:register"]
    assert agents == ["vtx_crs.agent:AGENT"]


def test_discover_entry_points_empty(tmp_path: Path):
    """No entry points returns empty lists."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'foo'\n", encoding="utf-8")
    extensions, agents = _discover_entry_points(tmp_path)
    assert extensions == []
    assert agents == []


def test_discover_package_layout(tmp_path: Path):
    """Package layout discovery finds vtx_extensions/ and vtx_agent/ dirs."""
    pkg = tmp_path / "my_pkg"
    pkg.mkdir()

    ext_dir = pkg / "vtx_extensions"
    ext_dir.mkdir()
    (ext_dir / "__init__.py").write_text("def register(api): pass\n", encoding="utf-8")

    agent_dir = pkg / "vtx_agent"
    agent_dir.mkdir()
    (agent_dir / "__init__.py").write_text("AGENT = None\n", encoding="utf-8")

    extensions, agents = _discover_package_layout(pkg)
    assert extensions == ["my_pkg.vtx_extensions:register"]
    assert agents == ["my_pkg.vtx_agent:AGENT"]


def test_discover_package_layout_empty(tmp_path: Path):
    pkg = tmp_path / "empty_pkg"
    pkg.mkdir()
    extensions, agents = _discover_package_layout(pkg)
    assert extensions == []
    assert agents == []
