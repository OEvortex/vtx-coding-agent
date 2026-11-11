import pytest

from vtx.coding_agent.tools.apply_patch import ApplyPatchParams, ApplyPatchTool, PatchEdit, PatchOp


@pytest.fixture
def patch_tool():
    return ApplyPatchTool()


@pytest.fixture
def text_file(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("line1\nline2\nline3\nline2\nline5")
    return f


def _params(*ops: PatchOp) -> ApplyPatchParams:
    return ApplyPatchParams(ops=list(ops))


@pytest.mark.asyncio
async def test_update_single_edit(patch_tool, text_file):
    result = await patch_tool.execute(
        _params(
            PatchOp(
                op="update", path=str(text_file), edits=[PatchEdit(old="line2", new="replaced")]
            )
        )
    )
    assert result.success
    assert text_file.read_text() == "line1\nreplaced\nline3\nline2\nline5"
    assert result.file_changes is not None
    assert result.file_changes[0].path == str(text_file)


@pytest.mark.asyncio
async def test_update_flat_old_new(patch_tool, text_file):
    result = await patch_tool.execute(
        _params(PatchOp(op="update", path=str(text_file), old="line2", new="flat"))
    )
    assert result.success
    assert text_file.read_text() == "line1\nflat\nline3\nline2\nline5"


@pytest.mark.asyncio
async def test_update_flat_replace_all(patch_tool, text_file):
    result = await patch_tool.execute(
        _params(PatchOp(op="update", path=str(text_file), old="line2", new="x", replace_all=True))
    )
    assert result.success
    assert text_file.read_text() == "line1\nx\nline3\nx\nline5"


@pytest.mark.asyncio
async def test_update_flat_and_edits_combined(patch_tool, text_file):
    result = await patch_tool.execute(
        _params(
            PatchOp(
                op="update",
                path=str(text_file),
                old="line5",
                new="line6",
                edits=[PatchEdit(old="line1", new="LINE1")],
            )
        )
    )
    assert result.success
    assert text_file.read_text() == "LINE1\nline2\nline3\nline2\nline6"


@pytest.mark.asyncio
async def test_update_missing_old_new_pair(patch_tool, text_file):
    result = await patch_tool.execute(
        _params(PatchOp(op="update", path=str(text_file), old="line2"))
    )
    assert not result.success
    assert "together" in result.result


@pytest.mark.asyncio
async def test_update_replace_all(patch_tool, text_file):
    result = await patch_tool.execute(
        _params(
            PatchOp(
                op="update",
                path=str(text_file),
                edits=[PatchEdit(old="line2", new="replaced", replace_all=True)],
            )
        )
    )
    assert result.success
    assert text_file.read_text() == "line1\nreplaced\nline3\nreplaced\nline5"


@pytest.mark.asyncio
async def test_update_not_found(patch_tool, text_file):
    result = await patch_tool.execute(
        _params(
            PatchOp(
                op="update",
                path=str(text_file),
                edits=[PatchEdit(old="nonexistent", new="replaced")],
            )
        )
    )
    assert not result.success
    assert "not found" in result.result


@pytest.mark.asyncio
async def test_update_file_not_found(patch_tool, tmp_path):
    result = await patch_tool.execute(
        _params(
            PatchOp(
                op="update",
                path=str(tmp_path / "nonexistent.py"),
                edits=[PatchEdit(old="line1", new="replaced")],
            )
        )
    )
    assert not result.success
    assert "file not found" in result.result


@pytest.mark.asyncio
async def test_update_multiline(patch_tool, text_file):
    result = await patch_tool.execute(
        _params(
            PatchOp(
                op="update",
                path=str(text_file),
                edits=[PatchEdit(old="line2\nline3", new="new2\nnew3\nnew4")],
            )
        )
    )
    assert result.success
    assert text_file.read_text() == "line1\nnew2\nnew3\nnew4\nline2\nline5"


@pytest.mark.asyncio
async def test_create_file(patch_tool, tmp_path):
    target = tmp_path / "pkg" / "helpers.py"
    result = await patch_tool.execute(
        _params(PatchOp(op="create", path=str(target), content="def slugify(s):\n    return s\n"))
    )
    assert result.success
    assert target.read_text() == "def slugify(s):\n    return s\n"


@pytest.mark.asyncio
async def test_create_existing_fails(patch_tool, text_file):
    result = await patch_tool.execute(
        _params(PatchOp(op="create", path=str(text_file), content="x"))
    )
    assert not result.success
    assert "already exists" in result.result


@pytest.mark.asyncio
async def test_delete_file(patch_tool, text_file):
    result = await patch_tool.execute(_params(PatchOp(op="delete", path=str(text_file))))
    assert result.success
    assert not text_file.exists()


@pytest.mark.asyncio
async def test_move_file(patch_tool, text_file, tmp_path):
    dest = tmp_path / "renamed.py"
    result = await patch_tool.execute(
        _params(PatchOp(op="move", path=str(text_file), new_path=str(dest)))
    )
    assert result.success
    assert not text_file.exists()
    assert dest.read_text() == "line1\nline2\nline3\nline2\nline5"


@pytest.mark.asyncio
async def test_multi_op_atomic(patch_tool, tmp_path):
    f1 = tmp_path / "a.py"
    f1.write_text("alpha")
    f2 = tmp_path / "b.py"
    result = await patch_tool.execute(
        _params(
            PatchOp(op="update", path=str(f1), edits=[PatchEdit(old="alpha", new="beta")]),
            PatchOp(op="delete", path=str(f2)),  # fails: b.py doesn't exist
        )
    )
    assert not result.success
    # Atomic: the first op's change must not have been written.
    assert f1.read_text() == "alpha"


@pytest.mark.asyncio
async def test_multi_file_ops(patch_tool, tmp_path):
    f1 = tmp_path / "a.py"
    f1.write_text("one\n")
    f2 = tmp_path / "b.py"
    f2.write_text("two\n")
    f3 = tmp_path / "c.py"
    result = await patch_tool.execute(
        _params(
            PatchOp(op="update", path=str(f1), edits=[PatchEdit(old="one", new="ONE")]),
            PatchOp(op="update", path=str(f2), edits=[PatchEdit(old="two", new="TWO")]),
            PatchOp(op="create", path=str(f3), content="three\n"),
        )
    )
    assert result.success
    assert f1.read_text() == "ONE\n"
    assert f2.read_text() == "TWO\n"
    assert f3.read_text() == "three\n"
    assert result.file_changes is not None and len(result.file_changes) == 3
