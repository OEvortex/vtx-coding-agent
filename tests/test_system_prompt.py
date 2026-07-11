from vtx import Config, reset_config, set_config
from vtx.context import Context
from vtx.loop import build_system_prompt
from vtx.tools import all_tools


def test_system_prompt_includes_guidelines():
    set_config(Config({}))
    try:
        prompt = build_system_prompt("/tmp", Context("/tmp"), tools=all_tools)
    finally:
        reset_config()

    assert "find for files by glob" in prompt
    assert "read (not cat/head/tail)" in prompt
    assert "edit (not sed/awk)" in prompt
    assert "write for new files/rewrites" in prompt
    assert "bash for git/builds/tests/scripts" in prompt
    assert "Session logs: JSONL in ~/.vtx/sessions" in prompt
    # Exactly one tool-usage section header.
    assert prompt.count("# Tool usage\n\n") == 1
    tool_usage = prompt.split("# Tool usage", 1)[1]
    assert "  - read (not cat" not in tool_usage
    assert "- read (not cat/head/tail)" in tool_usage


def test_system_prompt_includes_cwd():
    prompt = build_system_prompt("/test/dir", Context("/test/dir"))
    assert "/test/dir" in prompt


def test_system_prompt_excludes_git_context_by_default():
    set_config(Config({}))
    try:
        prompt = build_system_prompt("/tmp", Context("/tmp"))
    finally:
        reset_config()

    # The actual git-context block contains a "Current branch:" line; that is
    # the unambiguous signal that the section is present (CONTEXT_AWARENESS
    # references the literal block name in backticks, so a substring match on
    # "<git-status>" would not be specific enough).
    assert "Current branch:" not in prompt


def test_system_prompt_includes_git_context_when_enabled(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    set_config(Config({"llm": {"system_prompt": {"git_context": True}}}))
    try:
        prompt = build_system_prompt(str(repo), Context(str(repo)))
    finally:
        reset_config()

    # Non-git directory should still omit the section
    assert "Current branch:" not in prompt
