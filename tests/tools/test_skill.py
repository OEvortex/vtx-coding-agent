import pytest

from vtx.tools.skill import SkillParams, SkillTool


@pytest.fixture
def skill_tool():
    return SkillTool()


@pytest.mark.asyncio
async def test_skill_list(skill_tool):
    result = await skill_tool.execute(SkillParams(action="list"))
    assert result.success
    assert "review [bundled]:" in result.result


@pytest.mark.asyncio
async def test_skill_view(skill_tool):
    result = await skill_tool.execute(SkillParams(action="view", name="review"))
    assert result.success
    assert "Review code changes" in result.result


@pytest.mark.asyncio
async def test_skill_create_edit_patch_delete(skill_tool, tmp_path, monkeypatch):
    # Set CWD to a temp directory so we don't dirty the workspace
    monkeypatch.chdir(tmp_path)

    # 1. Create skill
    create_content = (
        "---\nname: my-test-skill\ndescription: Test skill description\n---\nHello test skill.\n"
    )
    result = await skill_tool.execute(
        SkillParams(action="create", name="my-test-skill", content=create_content)
    )
    assert result.success
    assert "created at" in result.result

    # 2. View skill
    result = await skill_tool.execute(SkillParams(action="view", name="my-test-skill"))
    assert result.success
    assert "Hello test skill" in result.result

    # 3. Edit skill
    edit_content = (
        "---\nname: my-test-skill\ndescription: Updated description\n---\nHello edited skill.\n"
    )
    result = await skill_tool.execute(
        SkillParams(action="edit", name="my-test-skill", content=edit_content)
    )
    assert result.success
    assert "updated at" in result.result

    # Check edit
    result = await skill_tool.execute(SkillParams(action="view", name="my-test-skill"))
    assert result.success
    assert "Hello edited skill" in result.result

    # 4. Patch skill
    result = await skill_tool.execute(
        SkillParams(
            action="patch",
            name="my-test-skill",
            old_string="Hello edited skill.",
            new_string="Hello patched skill.",
        )
    )
    assert result.success
    assert "Successfully patched" in result.result

    # Check patch
    result = await skill_tool.execute(SkillParams(action="view", name="my-test-skill"))
    assert result.success
    assert "Hello patched skill" in result.result

    # 5. Delete skill
    result = await skill_tool.execute(SkillParams(action="delete", name="my-test-skill"))
    assert result.success
    assert "deleted" in result.result

    # Check deleted
    result = await skill_tool.execute(SkillParams(action="view", name="my-test-skill"))
    assert not result.success
    assert "not found" in result.result


@pytest.mark.asyncio
async def test_skill_builtin_modify_prevented(skill_tool):
    result = await skill_tool.execute(
        SkillParams(action="edit", name="review", content="new content")
    )
    assert not result.success
    assert "Cannot modify built-in skill" in result.result

    result = await skill_tool.execute(SkillParams(action="delete", name="review"))
    assert not result.success
    assert "Cannot modify built-in skill" in result.result
