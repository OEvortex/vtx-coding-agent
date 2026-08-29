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
        "my",
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


def test_pi_style_tool_ui_edit_card() -> None:
    from vtx.openjarvis.tui.tool_ui import build_result_ui

    res = build_result_ui(
        result="  context line\n- old line\n+ new line",
        success=True,
        elapsed_s=0.08,
        tool_name="edit",
        tool_data={"path": "src/config.py"},
    )
    assert res["ui_details"] is not None
    assert "╭─ ➔ Edit ✓ · src/config.py" in res["ui_details"]
    assert "├─ Diff · +1 -1" in res["ui_details"]
    assert "╰─ 1 file · +1 -1 · 0.08s" in res["ui_details"]


def test_pi_style_tool_ui_write_card() -> None:
    from vtx.openjarvis.tui.tool_ui import build_result_ui

    res = build_result_ui(
        result="def hello():\n    return 42",
        success=True,
        elapsed_s=0.03,
        tool_name="write",
        tool_data={"path": "src/new.py", "content": "def hello():\n    return 42"},
    )
    assert res["ui_details"] is not None
    assert "╭─ ✎ Write ✓ · src/new.py" in res["ui_details"]
    assert "├─ Written 2 lines" in res["ui_details"]
    assert "╰─ 1 file · 2 lines · 0.03s" in res["ui_details"]


def test_pi_style_tool_ui_web_tree() -> None:
    from vtx.openjarvis.tui.tool_ui import build_result_ui

    res = build_result_ui(
        result="Python Docs - https://docs.python.org\nReal Python - https://realpython.com",
        success=True,
        elapsed_s=0.45,
        tool_name="web",
        tool_data={"query": "python async"},
    )
    assert res["ui_summary"] is not None
    assert "🌐 Web: python async (2 results) · 0.45s" in res["ui_summary"]
    assert res["ui_details"] is not None
    assert "  ├─ Python Docs" in res["ui_details"]
    assert "  └─ Real Python" in res["ui_details"]


def test_pi_style_tool_ui_services() -> None:
    from vtx.openjarvis.tui.tool_ui import build_result_ui

    # Cron
    cron_res = build_result_ui(
        result="Cron task created successfully",
        success=True,
        elapsed_s=0.01,
        tool_name="cron",
        tool_data={"action": "add", "name": "backup", "every_seconds": 3600},
    )
    assert cron_res["ui_summary"] is not None
    assert "⏱ Cron: add 'backup' every 3600s · 0.01s" in cron_res["ui_summary"]

    # Message
    msg_res = build_result_ui(
        result="Message sent",
        success=True,
        elapsed_s=0.02,
        tool_name="message",
        tool_data={"channel": "slack", "message": "hello team"},
    )
    assert msg_res["ui_summary"] is not None
    assert "✉ Message: slack → hello team · 0.02s" in msg_res["ui_summary"]


def test_adapted_vtx_read_tool_execution(tmp_path) -> None:
    from vtx.coding_agent.tools.read import ReadParams, ReadTool
    from vtx.openjarvis.tools import adapt_tool

    test_file = tmp_path / "test.txt"
    test_file.write_text("line 1\nline 2\nline 3\n")

    tool = adapt_tool(ReadTool())
    # Should accept and safely execute even when tool_call_id is passed
    params = ReadParams(path=str(test_file))
    res = asyncio.run(tool.execute(params, tool_call_id="call_123"))
    assert res.success is True
    assert "line 1" in res.result
    assert "● Read" in (res.ui_summary or "")


def test_tool_block_header_suppression_with_boxed_card() -> None:
    import vtx.openjarvis.tui.branding  # noqa: F401
    from vtx.tui.blocks import ToolBlock

    block = ToolBlock(name="exec", call_msg="ls -la")
    # Before result, regular header with call_msg
    header_text = block._format_header().plain
    assert "exec" in header_text or "ls -la" in header_text

    # After boxed result, outer header is suppressed
    block._ui_details = "╭─ ➔ Exec ✓ ─────────────────────────╮\n│ $ ls -la │\n╰─ Exit 0 ─╯"
    header_text_boxed = block._format_header().plain
    assert header_text_boxed == ""


def test_titanium_theme_registered() -> None:
    from vtx.tui.themes import get_theme, get_theme_ids

    theme_ids = get_theme_ids()
    assert "titanium" in theme_ids
    assert "titanium-light" in theme_ids

    titanium = get_theme("titanium")
    assert titanium.colors.accent == "#00b4ff"
    assert titanium.colors.success == "#00ff88"
    assert titanium.colors.bg == "#0f1216"
