"""Tests for the OpenJarvis package: version, tool curatation, and apply_patch."""

from __future__ import annotations

import asyncio

from typing import Any

from vtx.openjarvis.tools import OPENJARVIS_DEFAULT_TOOLS, OPENJARVIS_KEEP_TOOLS, register_with_vtx
from vtx.openjarvis.tools.apply_patch import ApplyPatchTool
from vtx.openjarvis.version import VERSION


def test_version_is_0_2_0() -> None:
    assert VERSION == "0.2.0"


def test_openjarvis_default_tools_are_curatted() -> None:
    expected = [
        "read",
        "edit",
        "write",
        "exec",
        "apply_patch",
        "find",
        "skill",
        "web",
        "ask_user",
        "task",
        "goal",
        "cron",
        "list_exec_sessions",
        "write_stdin",
        "message",
    ]
    assert list(OPENJARVIS_DEFAULT_TOOLS) == expected
    # The kept OpenJarvis-specific tools must be a subset of the default list.
    assert set(OPENJARVIS_DEFAULT_TOOLS) >= OPENJARVIS_KEEP_TOOLS


def test_register_with_vtx_exposes_only_curatted_tools() -> None:
    register_with_vtx()
    from vtx.coding_agent.tools import DEFAULT_TOOLS, tools_by_name

    assert set(DEFAULT_TOOLS) == set(OPENJARVIS_DEFAULT_TOOLS)
    assert set(tools_by_name) == set(OPENJARVIS_DEFAULT_TOOLS)
    # bash / grep are dropped in favour of exec + the curated list.
    assert "bash" not in tools_by_name
    assert "grep" not in tools_by_name
    # OpenJarvis extras are dropped.
    for dropped in (
        "edit_file",
        "read_file",
        "write_file",
        "list_dir",
        "find_files",
        "web_search",
    ):
        assert dropped not in tools_by_name


def test_register_with_vtx_patches_harness_registry() -> None:
    """The TUI/headless assemble tools from the harness registry — it must be patched."""
    register_with_vtx()
    from vtx.ai.agent.tools import get_default_tools, get_tools_with_extensions

    assert set(get_default_tools()) == set(OPENJARVIS_DEFAULT_TOOLS)
    names = [t.name for t in get_tools_with_extensions()]
    assert set(names) == set(OPENJARVIS_DEFAULT_TOOLS)
    assert "bash" not in names
    assert "grep" not in names


def test_adapter_builds_params_for_openjarvis_tool() -> None:
    register_with_vtx()
    from vtx.coding_agent.tools import tools_by_name

    tool = tools_by_name["apply_patch"]
    # The adapter exposes the BaseTool-style params model the agent expects.
    model = tool.params(edits=[{"path": "x.txt", "action": "add", "new_text": "hi"}], dry_run=True)
    assert model.edits[0]["path"] == "x.txt"
    assert model.dry_run is True


# ---------------------------------------------------------------------------
# apply_patch tool logic
# ---------------------------------------------------------------------------


def _run(tool: ApplyPatchTool, **kwargs: Any) -> str:
    return asyncio.run(tool.execute(**kwargs))


def test_apply_patch_add_new_file(tmp_path) -> None:
    tool = ApplyPatchTool(workspace=tmp_path)
    out = _run(
        tool, edits=[{"path": "a.txt", "action": "add", "new_text": "hello\n"}], dry_run=False
    )
    assert "Patch applied" in out
    assert (tmp_path / "a.txt").read_text() == "hello\n"


def test_apply_patch_replace_text(tmp_path) -> None:
    tool = ApplyPatchTool(workspace=tmp_path)
    (tmp_path / "a.txt").write_text("foo bar baz\n", encoding="utf-8")
    out = _run(
        tool,
        edits=[{"path": "a.txt", "action": "replace", "old_text": "bar", "new_text": "QUX"}],
        dry_run=False,
    )
    assert "Patch applied" in out
    assert (tmp_path / "a.txt").read_text() == "foo QUX baz\n"


def test_apply_patch_dry_run_does_not_write(tmp_path) -> None:
    tool = ApplyPatchTool(workspace=tmp_path)
    out = _run(
        tool, edits=[{"path": "a.txt", "action": "add", "new_text": "hello\n"}], dry_run=True
    )
    assert "dry-run" in out
    assert not (tmp_path / "a.txt").exists()


def test_apply_patch_replace_missing_old_text_errors(tmp_path) -> None:
    tool = ApplyPatchTool(workspace=tmp_path)
    (tmp_path / "a.txt").write_text("foo bar\n", encoding="utf-8")
    out = _run(
        tool,
        edits=[{"path": "a.txt", "action": "replace", "old_text": "nope", "new_text": "x"}],
        dry_run=False,
    )
    assert "Error" in out
    assert "not found" in out


def test_apply_patch_multiple_matches_errors(tmp_path) -> None:
    tool = ApplyPatchTool(workspace=tmp_path)
    (tmp_path / "a.txt").write_text("x x x\n", encoding="utf-8")
    out = _run(
        tool,
        edits=[{"path": "a.txt", "action": "replace", "old_text": "x", "new_text": "y"}],
        dry_run=False,
    )
    assert "Error" in out
    assert "multiple times" in out


def test_apply_patch_validation_errors() -> None:
    tool = ApplyPatchTool()
    assert "must provide edits" in _run(tool, edits=None)
    assert "each edit must be an object" in _run(tool, edits=[["not", "an", "object"]])
    assert "path required" in _run(tool, edits=[{"action": "add"}])
    assert "action required" in _run(tool, edits=[{"path": "a.txt"}])


# ---------------------------------------------------------------------------
# pi-style TUI formatting & theme tests
# ---------------------------------------------------------------------------


def test_pi_style_tool_ui_boxed_exec() -> None:
    from vtx.openjarvis.tui.tool_ui import build_result_ui

    res = build_result_ui(
        result="v0.1.4\nfeat: strict boxed tool-card\nExit 0",
        success=True,
        elapsed_s=0.04,
        tool_name="exec",
        tool_data={"command": "git log --oneline -15"},
    )
    assert res["ui_details"] is not None
    assert "╭─ ➔ Exec ✓" in res["ui_details"]
    assert "$ git log --oneline -15" in res["ui_details"]
    assert "├─ Response" in res["ui_details"]
    assert "╰─ Exit 0 · 0.04s" in res["ui_details"]


def test_pi_style_tool_ui_file_tree() -> None:
    from vtx.openjarvis.tui.tool_ui import build_result_ui

    res = build_result_ui(
        result=".git/\n.github/\n.gitignore\nREADME.md",
        success=True,
        elapsed_s=0.01,
        tool_name="find",
        tool_data={"path": "src", "pattern": "*.py"},
    )
    assert res["ui_details"] is not None
    assert "Q Glob: *.py 4 files · in src" in res["ui_details"]
    assert "  A .git/" in res["ui_details"]
    assert "  * .gitignore" in res["ui_details"]


def test_pi_style_tool_ui_read_summary() -> None:
    from vtx.openjarvis.tui.tool_ui import build_result_ui

    res = build_result_ui(
        result="def hello():\n    print('world')",
        success=True,
        elapsed_s=0.05,
        tool_name="read",
        tool_data={"path": "ROADMAP.md", "offset": 1, "limit": 60},
    )
    assert res["ui_summary"] is not None
    assert "● Read ROADMAP.md:1-60 · 0.05s" in res["ui_summary"]


def test_titanium_theme_registered() -> None:
    from vtx.tui.themes import get_theme, get_theme_ids

    theme_ids = get_theme_ids()
    assert "titanium" in theme_ids
    assert "titanium-light" in theme_ids

    titanium = get_theme("titanium")
    assert titanium.colors.accent == "#00b4ff"
    assert titanium.colors.success == "#00ff88"
    assert titanium.colors.bg == "#0f1216"
