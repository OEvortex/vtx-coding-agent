"""Regression tests for RLM-mode context diet: compact skills index, prompt
size guard, mode-aware tool-result budget, and read_file byte cap."""

import pytest

from vtx.ai.agent.context import Context, formatted_skills, formatted_skills_index
from vtx.ai.agent.context_governance import (
    _MAX_TOOL_RESULT_CHARS,
    _RLM_MAX_TOOL_RESULT_CHARS,
    prepare_for_model,
)
from vtx.ai.agent.prompts.builder import build_system_prompt
from vtx.coding_agent.prompts.rlm import build_rlm_system_prompt
from vtx.core.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage


def _assistant_with_call(call_id: str = "call-1") -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCall(id=call_id, name="ipython", arguments={"code": "1+1"})]
    )


def _big_result(text: str, call_id: str = "call-1") -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call_id, tool_name="ipython", content=[TextContent(text=text)]
    )


def test_skills_index_is_compact_routing_list(tmp_path):
    ctx = Context.load(str(tmp_path))
    assert ctx.skills, "expected bundled/discovered skills in test env"
    full = formatted_skills(ctx.skills)
    index = formatted_skills_index(ctx.skills)
    assert "## Skills index" in index
    assert "read_file" in index  # on-demand loading instruction
    for skill in ctx.skills:
        if skill.include_in_prompt:
            assert skill.name in index
    # The index must be far smaller than the full catalog: in a real install
    # this is the ~5k-token per-turn saving in RLM mode (measured 24k -> 5.7k
    # chars); the bound below also holds for the small isolated test catalog.
    assert len(index) < len(full) // 2
    assert len(index) < 8000


def test_skills_index_truncates_long_descriptions():
    from vtx.ai.agent.context.skills import Skill

    skill = Skill(name="s", description="word " * 200, path="/tmp/x/SKILL.md")
    index = formatted_skills_index([skill], max_desc_chars=60)
    line = next(line for line in index.splitlines() if line.startswith("- s"))
    assert len(line) < 100
    assert line.endswith("...")


def test_rlm_full_prompt_size_regression_guard(tmp_path):
    import vtx.ai.config as config_mod

    old = config_mod.config._parsed.mode
    config_mod.config._parsed.mode = "code_first"
    try:
        prompt = build_system_prompt(str(tmp_path), tools=[])
    finally:
        config_mod.config._parsed.mode = old
    # Was 52076 chars (~13k tokens); the skills index must keep it well below.
    assert len(prompt) < 40000
    assert "## Skills index" in prompt
    # Tool-first "read tool" mandate language must not leak into RLM mode.
    assert "MUST load it with the read tool" not in prompt


def test_rlm_base_prompt_keep_pins_and_guardrails():
    prompt = build_rlm_system_prompt()
    for pin in (
        "ipython",
        "REPL",
        "read_file",
        "write_file",
        "run_bash",
        "goal_get",
        "rlm.spawn",
        "agent_message",
        "rlm.harness",
        "refine.run",
    ):
        assert pin in prompt
    assert "rlm(" not in prompt
    assert "mcp" not in prompt.lower()
    # never-dump guardrail for the context-as-variable footgun
    assert "context.messages" in prompt
    assert "Never print" in prompt


def test_tool_result_budget_is_mode_aware(monkeypatch):
    import vtx.ai.config as config_mod

    big = "x" * (_MAX_TOOL_RESULT_CHARS + 5000)
    messages = [UserMessage(content="hi"), _assistant_with_call(), _big_result(big)]

    # tool-first: unchanged 200k budget, first-N-kept semantics preserved
    monkeypatch.setattr(config_mod.config._parsed, "mode", "tool_first")
    repaired = prepare_for_model(messages)
    text = repaired[2].content[0].text
    assert len(text) <= _MAX_TOOL_RESULT_CHARS + 300
    assert "tool output truncated" in text

    # rlm: tighter budget since everything funnels through one tool
    monkeypatch.setattr(config_mod.config._parsed, "mode", "code_first")
    repaired = prepare_for_model(messages)
    text = repaired[2].content[0].text
    assert len(text) <= _RLM_MAX_TOOL_RESULT_CHARS + 300
    assert "narrower slice" in text

    # under budget: untouched in both modes
    small = [UserMessage(content="hi"), _assistant_with_call(), _big_result("ok")]
    assert prepare_for_model(small)[2].content[0].text == "ok"


@pytest.mark.asyncio
async def test_kernel_read_file_byte_cap_and_paging(tmp_path):
    from vtx.ai.agent.ipython_manager import IpythonKernel

    kernel = IpythonKernel("test-kernel-read-cap", cwd=str(tmp_path))
    await kernel.start()
    try:
        big = tmp_path / "big.txt"
        big.write_text("A" * (80 * 1024) + "\n" + "B" * 100 + "\n")

        out, err = await kernel.execute(f"print(len(read_file({str(big)!r})))", timeout=10.0)
        assert not err
        shown = int(out.strip())
        assert shown <= 52 * 1024  # 50KB cap + note

        out, err = await kernel.execute(f"print(read_file({str(big)!r}))", timeout=10.0)
        assert not err
        assert "read truncated at 50KB" in out
        assert "offset=" in out

        small = tmp_path / "small.txt"
        small.write_text("hello\nworld\n")
        out, err = await kernel.execute(f"print(repr(read_file({str(small)!r})))", timeout=10.0)
        assert not err
        assert "hello" in out and "more lines" not in out
    finally:
        await kernel.close()
