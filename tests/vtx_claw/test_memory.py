from __future__ import annotations

from pathlib import Path

import pytest

from vtx_claw.memory import MemoryManager


def test_remember_and_recall(tmp_path: Path):
    mgr = MemoryManager(tmp_path)
    mgr.remember("u1", "name", "Alice")
    mgr.remember("u1", "color", "blue")
    all_entries = mgr.recall("u1")
    assert len(all_entries) == 2
    assert all_entries[0]["key"] == "color"
    assert all_entries[0]["value"] == "blue"


def test_recall_filter_by_query(tmp_path: Path):
    mgr = MemoryManager(tmp_path)
    mgr.remember("u1", "pet", "dog")
    mgr.remember("u1", "food", "pizza")
    hits = mgr.recall("u1", query="pet")
    assert len(hits) == 1
    assert hits[0]["value"] == "dog"


def test_get_all_returns_entries(tmp_path: Path):
    mgr = MemoryManager(tmp_path)
    mgr.remember("u2", "x", "1")
    assert len(mgr.get_all("u2")) == 1


def test_format_for_prompt(tmp_path: Path):
    mgr = MemoryManager(tmp_path)
    mgr.remember("u3", "k", "v")
    out = mgr.format_for_prompt("u3")
    assert "User memories:" in out
    assert "k: v" in out


def test_delete_entry(tmp_path: Path):
    mgr = MemoryManager(tmp_path)
    mgr.remember("u4", "a", "1")
    mgr.remember("u4", "b", "2")
    assert mgr.delete("u4", "a")
    assert len(mgr.get_all("u4")) == 1
    assert not mgr.delete("u4", "nonexistent")


def test_daily_log_file_created(tmp_path: Path):
    mgr = MemoryManager(tmp_path)
    mgr.remember("u5", "k", "v")
    today = "2026-06-29"
    log = tmp_path / f"{today}.md"
    assert log.exists()
    content = log.read_text()
    assert "u5" in content
    assert "k" in content


def test_load_tools_md_missing(tmp_path: Path):
    mgr = MemoryManager(tmp_path)
    assert mgr.load_tools_md() == ""


def test_load_tools_md_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".vtx" / "claw").mkdir(parents=True)
    (home / ".vtx" / "claw" / "TOOLS.md").write_text("my tools notes")
    monkeypatch.setattr(Path, "home", lambda: home)
    mgr = MemoryManager(tmp_path)
    assert "my tools notes" in mgr.load_tools_md()
