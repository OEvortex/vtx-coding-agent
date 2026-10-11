"""Tests for profile composition, bundles, and dump-config."""

from __future__ import annotations

import pytest

from vtx.plugins import DEFAULT_PROFILE, PatchError, dump_config, load_profile
from vtx.plugins.builtins import BUNDLES, PROFILE_TEMPLATES
from vtx.plugins.profile import init_profile, profile_dir, profile_file, read_bundles


def test_shipped_profiles_stack_only_known_bundles():
    for name, bundles in PROFILE_TEMPLATES.items():
        assert set(bundles) <= set(BUNDLES), name


def test_the_default_profile_is_known():
    assert DEFAULT_PROFILE in PROFILE_TEMPLATES


def test_base_is_the_first_layer_of_every_non_minimal_profile():
    for name, bundles in PROFILE_TEMPLATES.items():
        if name == "minimal":
            continue
        assert bundles[0] == "base", name


def test_every_bundle_row_is_individually_addressable():
    for name, rows in BUNDLES.items():
        ids = [row.id for row in rows]
        assert len(ids) == len(set(ids)), f"{name} has a duplicate row id"


def test_load_profile_composes_bundles_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    rows = load_profile("default")
    assert rows[0].id == "tools"
    assert {row.id for row in rows} >= {"tools", "tool-read", "ui", "commands"}


def test_load_profile_prefers_the_profiles_own_bundle_list(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    directory = tmp_path / "profiles" / "mine"
    directory.mkdir(parents=True)
    (directory / "bundle.txt").write_text("minimal\n", encoding="utf-8")
    rows = load_profile("mine")
    assert [row.id for row in rows] == ["tools", "llm", "tool-read", "tool-bash"]


def test_a_profile_unknown_bundle_fails_loud(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    directory = tmp_path / "profiles" / "bad"
    directory.mkdir(parents=True)
    (directory / "bundle.txt").write_text("nope\n", encoding="utf-8")
    with pytest.raises(PatchError, match="unknown bundle"):
        load_profile("bad")


def test_the_profile_patch_layer_applies_after_the_bundles(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    directory = tmp_path / "profiles" / "p"
    directory.mkdir(parents=True)
    (directory / "bundle.txt").write_text("base\n", encoding="utf-8")
    (directory / "cordis.yml").write_text(
        "- override:\n    - id: tool-grep\n      disabled: true\n"
        "- override:\n    - id: tool-bash\n      config:\n        timeout: 5\n",
        encoding="utf-8",
    )
    rows = {row.id: row for row in load_profile("p")}
    assert rows["tool-grep"].disabled is True
    assert rows["tool-bash"].config == {"timeout": 5}


def test_a_cli_patch_overlay_applies_after_the_profile_layer(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    overlay = tmp_path / "overlay.yml"
    overlay.write_text("- remove:\n    - tool-grep\n", encoding="utf-8")
    rows = {row.id: row for row in load_profile("default", [overlay])}
    assert "tool-grep" not in rows


def test_a_profile_patch_must_be_a_list(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    directory = tmp_path / "profiles" / "p"
    directory.mkdir(parents=True)
    (directory / "bundle.txt").write_text("base\n", encoding="utf-8")
    (directory / "cordis.yml").write_text("just-a-string\n", encoding="utf-8")
    with pytest.raises(PatchError, match="must be a YAML list"):
        load_profile("p")


def test_dump_config_returns_one_record_per_row(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    records = dump_config("default")
    assert records
    assert all({"id", "name", "disabled", "config"} <= set(r) for r in records)


def test_init_profile_writes_both_files(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    directory = init_profile("fresh")
    assert (directory / "bundle.txt").exists()
    assert (directory / "cordis.yml").exists()
    assert read_bundles("fresh") == ["base", "tui"]


def test_a_profile_name_cannot_escape_the_profiles_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    with pytest.raises(PatchError, match="invalid profile name"):
        profile_dir("../escape")
    with pytest.raises(PatchError, match="invalid profile name"):
        profile_file("a/b")


def test_read_bundles_falls_back_to_the_shipped_template(tmp_path, monkeypatch):
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    assert read_bundles("headless") == ["base"]
