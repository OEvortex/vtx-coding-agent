"""apply_patch: diff helpers, param coercion and the three edit ops."""

import pytest
from vx.tools.apply_patch import (
    ApplyPatchParams,
    ApplyPatchTool,
    PatchOp,
    format_diff_display,
    generate_diff,
    generate_diff_preview,
)


class TestGenerateDiff:
    def test_counts_added_removed(self):
        diff, added, removed = generate_diff("a\nb\nc\n", "a\nx\nc\n")
        assert added == 1
        assert removed == 1
        assert "-b" in diff and "+x" in diff

    def test_identical_is_empty(self):
        _diff, added, removed = generate_diff("same\n", "same\n")
        assert added == 0 and removed == 0


class TestDiffPreview:
    def test_marks_change_rows(self):
        rows = generate_diff_preview("one\ntwo\nthree\n", "one\nTWO\nthree\n")
        ops = {op for op, _ln, _t in rows}
        assert "+" in ops and "-" in ops

    def test_collapses_unchanged_runs(self):
        old = "\n".join(f"line{i}" for i in range(50))
        new = old.replace("line10", "CHANGED")
        rows = generate_diff_preview(old, new, context=2)
        markers = [r for r in rows if r[0] == "~"]
        assert markers, "long unchanged run should collapse to a ~ marker"


class TestFormatDiffDisplay:
    def test_colors_ops(self):
        diff, _, _ = generate_diff("a\n", "b\n")
        out = format_diff_display(diff)
        assert "[green]" in out and "[red]" in out


class TestParams:
    def test_flat_pairs_become_ops(self):
        params = ApplyPatchParams.model_validate(
            {"ops": ["path", "a.py", "old_str", "x", "new_str", "y"]}
        )
        assert params.ops[0].path == "a.py"
        assert params.ops[0].old_str == "x"
        assert params.ops[0].new_str == "y"

    def test_json_string_ops(self):
        params = ApplyPatchParams.model_validate(
            {"ops": '[{"path": "a.py", "new_file_content": "hi"}]'}
        )
        assert params.ops[0].new_file_content == "hi"

    def test_aliases_accepted(self):
        op = PatchOp.model_validate({"file_path": "a.py", "old": "x", "new": "y"})
        assert op.path == "a.py"
        assert op.old_str == "x"
        assert op.new_str == "y"


class TestExecute:
    @pytest.mark.asyncio
    async def test_create_file(self, tmp_path):
        tool = ApplyPatchTool()
        target = tmp_path / "new.py"
        result = await tool.execute(
            ApplyPatchParams.model_validate(
                {"ops": [{"path": str(target), "new_file_content": "print(1)\n"}]}
            )
        )
        assert result.success
        assert target.read_text() == "print(1)\n"
        assert result.file_changes and result.file_changes[0].added == 1

    @pytest.mark.asyncio
    async def test_search_replace(self, tmp_path):
        target = tmp_path / "f.py"
        target.write_text("alpha\nbeta\n")
        tool = ApplyPatchTool()
        result = await tool.execute(
            ApplyPatchParams.model_validate(
                {"ops": [{"path": str(target), "old_str": "beta", "new_str": "gamma"}]}
            )
        )
        assert result.success
        assert target.read_text() == "alpha\ngamma\n"

    @pytest.mark.asyncio
    async def test_missing_search_fails(self, tmp_path):
        target = tmp_path / "f.py"
        target.write_text("alpha\n")
        tool = ApplyPatchTool()
        result = await tool.execute(
            ApplyPatchParams.model_validate(
                {"ops": [{"path": str(target), "old_str": "nope", "new_str": "x"}]}
            )
        )
        assert not result.success
        assert "not found" in (result.result or "")

    @pytest.mark.asyncio
    async def test_insert_line(self, tmp_path):
        target = tmp_path / "f.py"
        target.write_text("one\nthree\n")
        tool = ApplyPatchTool()
        result = await tool.execute(
            ApplyPatchParams.model_validate(
                {"ops": [{"path": str(target), "insert_line": 1, "new_str": "two"}]}
            )
        )
        assert result.success
        assert target.read_text() == "one\ntwo\nthree\n"

    @pytest.mark.asyncio
    async def test_empty_ops_fails(self):
        result = await ApplyPatchTool().execute(ApplyPatchParams(ops=[]))
        assert not result.success

    @pytest.mark.asyncio
    async def test_multiple_ops_one_file_one_diff(self, tmp_path):
        target = tmp_path / "f.py"
        target.write_text("a\nb\nc\n")
        tool = ApplyPatchTool()
        result = await tool.execute(
            ApplyPatchParams.model_validate(
                {
                    "ops": [
                        {"path": str(target), "old_str": "a", "new_str": "A"},
                        {"path": str(target), "old_str": "c", "new_str": "C"},
                    ]
                }
            )
        )
        assert result.success
        assert target.read_text() == "A\nb\nC\n"
        # One diff group for the file, not one per op.
        groups = (result.ui_data or {}).get("diff_groups", [])
        assert len(groups) == 1
