"""Tests for the vtx.prompts package.

These exercise the modular prompt builder in isolation: each section
helper, the orchestrator's section ordering, and the override hooks
used by the runtime and tests.
"""

from __future__ import annotations

from vtx import Config, reset_config, set_config
from vtx.context import Context
from vtx.prompts import (
    CONTEXT_AWARENESS,
    DEFAULT_VTX_BASE,
    EDITING_CONSTRAINTS,
    ENV_HEADER,
    ERROR_RECOVERY,
    EXECUTION_DISCIPLINE,
    OUTPUT_FORMATTING,
    PROGRESS_UPDATES,
    SAFETY,
    TASK_COMPLETION,
    TOOL_USAGE_HEADER,
    TOOL_USE_ENFORCEMENT,
    VTX_GENERAL_RULES,
    VTX_IDENTITY,
    build_env_section,
    build_system_prompt,
    build_tool_guidelines_section,
)
from vtx.prompts.identity import _compose_default_base
from vtx.tools import all_tools

# ---------------------------------------------------------------------------
# identity section constants
# ---------------------------------------------------------------------------


def test_default_base_includes_all_sections_in_order():
    sections = [
        VTX_IDENTITY,
        CONTEXT_AWARENESS,
        OUTPUT_FORMATTING,
        EDITING_CONSTRAINTS,
        TOOL_USE_ENFORCEMENT,
        TASK_COMPLETION,
        EXECUTION_DISCIPLINE,
        ERROR_RECOVERY,
        SAFETY,
        PROGRESS_UPDATES,
        VTX_GENERAL_RULES,
    ]
    expected = "\n\n".join(sections)
    assert expected == DEFAULT_VTX_BASE
    assert _compose_default_base() == DEFAULT_VTX_BASE


def test_default_base_starts_with_identity():
    assert DEFAULT_VTX_BASE.startswith(VTX_IDENTITY)


def test_default_base_includes_general_rules():
    assert VTX_GENERAL_RULES in DEFAULT_VTX_BASE
    assert "~/.vtx/sessions" in DEFAULT_VTX_BASE


def test_default_base_includes_safety_rules():
    assert "rm -rf" in SAFETY
    assert "interactive" in SAFETY.lower()


# ---------------------------------------------------------------------------
# tooling section
# ---------------------------------------------------------------------------


def test_tool_guidelines_dedupes_and_preserves_order():
    guidelines = build_tool_guidelines_section(all_tools)
    assert guidelines.startswith(TOOL_USAGE_HEADER)
    # no nested bullet indentation
    for line in guidelines.splitlines():
        assert not line.startswith("  - "), line


def test_tool_guidelines_empty_when_no_tools():
    assert build_tool_guidelines_section(None) == ""
    assert build_tool_guidelines_section([]) == ""


# ---------------------------------------------------------------------------
# env section
# ---------------------------------------------------------------------------


def test_env_section_includes_context_details():
    section = build_env_section("/tmp")
    assert section.startswith(ENV_HEADER)
    assert "/tmp" in section
    # Context files section (per the user's JARVIS-style ask)
    assert "Working directory" in section
    assert "OS" in section
    assert "Python" in section
    assert "Vtx version" in section


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------


def test_build_system_prompt_uses_default_base_when_config_empty():
    set_config(Config({}))
    try:
        prompt = build_system_prompt("/tmp", Context("/tmp"), tools=all_tools)
    finally:
        reset_config()

    assert prompt.startswith(VTX_IDENTITY)
    assert VTX_GENERAL_RULES in prompt
    assert TOOL_USAGE_HEADER in prompt
    assert ENV_HEADER in prompt


def test_build_system_prompt_honors_explicit_base_override():
    set_config(Config({}))
    try:
        prompt = build_system_prompt(
            "/tmp", Context("/tmp"), tools=all_tools, base_content="CUSTOM_BASE"
        )
    finally:
        reset_config()

    assert prompt.startswith("CUSTOM_BASE")
    # The default identity is no longer in play
    assert VTX_IDENTITY not in prompt


def test_build_system_prompt_honors_explicit_git_flag():
    prompt = build_system_prompt(
        "/tmp", Context("/tmp"), tools=all_tools, include_git_context=False
    )
    assert "Current branch:" not in prompt

    prompt = build_system_prompt(
        "/tmp", Context("/tmp"), tools=all_tools, include_git_context=True
    )
    # /tmp isn't a git repo, so the git section stays empty even with flag on.
    assert "Current branch:" not in prompt


def test_build_system_prompt_section_order_is_stable():
    set_config(Config({}))
    try:
        prompt = build_system_prompt("/tmp", Context("/tmp"), tools=all_tools)
    finally:
        reset_config()

    # The base identity is the only one that appears once at the start.
    assert prompt.startswith(VTX_IDENTITY)
    # The last tool-usage header is the actual section, not a reference.
    tool_idx = prompt.rfind(TOOL_USAGE_HEADER)
    # The last env header is the actual section, not a reference in CONTEXT_AWARENESS.
    env_idx = prompt.rfind(ENV_HEADER)
    assert 0 < tool_idx < env_idx, (tool_idx, env_idx)


def test_build_system_prompt_includes_mandatory_skills_block(tmp_path):

    skill_dir = tmp_path / ".agents" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo description\ncategory: workflows\n---\n\nbody\n",
        encoding="utf-8",
    )
    ctx = Context.load(str(tmp_path))

    prompt = build_system_prompt(str(tmp_path), context=ctx, tools=all_tools)

    assert "## Skills (mandatory)" in prompt
    assert "Before replying, scan the skills below" in prompt
    assert "Only proceed without loading a skill if genuinely none are relevant" in prompt
    assert "<available_skills>" in prompt
    assert "  workflows:" in prompt
    assert "- demo: demo description" in prompt
    assert "</available_skills>" in prompt


def test_build_system_prompt_includes_bundled_skills(tmp_path):
    # No project or user skills — bundled skills must still be advertised
    # to the model in the <available_skills> block.
    ctx = Context.load(str(tmp_path))

    prompt = build_system_prompt(str(tmp_path), context=ctx, tools=all_tools)

    assert "- init:" in prompt
    assert "- review:" in prompt
    assert "- skill-builder:" in prompt
