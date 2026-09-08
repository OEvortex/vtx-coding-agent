"""Tests for RLM mode configuration, prompt assembly, and tool filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from vtx.ai.agent.runtime import ConversationRuntime
from vtx.ai.config import Config, ConfigSchema, set_config
from vtx.coding_agent.prompts.builder import build_system_prompt
from vtx.coding_agent.prompts.rlm import build_rlm_system_prompt


def test_rlm_system_prompt_contains_repl_guidance():
    prompt = build_rlm_system_prompt()
    assert "ipython" in prompt
    assert "REPL" in prompt
    assert "read_file" in prompt
    assert "write_file" in prompt
    assert "run_bash" in prompt
    assert "goal_get" in prompt
    assert "rlm(" in prompt


def test_build_system_prompt_uses_rlm_prompt_when_mode_is_rlm(tmp_path, monkeypatch):
    monkeypatch.setenv("VTX_SESSION_ID", "test-session")
    data = {
        "meta": {"config_version": 13},
        "llm": {
            "default_provider": "openai",
            "default_model": "gpt-4o",
            "default_base_url": "",
            "default_thinking_level": "off",
            "tool_call_idle_timeout_seconds": 180,
            "request_timeout_seconds": 600,
            "auth": {"openai_compat": "auto", "anthropic_compat": "auto"},
            "tls": {"insecure_skip_verify": False},
            "system_prompt": {"git_context": False, "ponytail": False},
        },
        "ui": {
            "theme": "gruvbox-dark",
            "collapse_thinking": True,
            "thinking_lines": "1",
            "colored_tool_badge": True,
            "show_welcome_shortcuts": True,
            "hidden_models": [],
            "model_provider_filter": "",
        },
        "compaction": {"on_overflow": "continue", "threshold_percent": 80},
        "agent": {"max_turns": 500, "default_context_window": 200000},
        "permissions": {"mode": "prompt"},
        "notifications": {"enabled": False, "volume": 0.5},
        "recap": {"enabled": True, "idle_seconds": 60},
        "last_selected": {
            "model_id": None,
            "provider": None,
            "thinking_level": None,
            "agent": None,
        },
        "recent_models": {"entries": []},
        "extensions": [],
        "agents": {"default": "", "switch_mode": "lock", "files": []},
        "task": {"subagent_presets": []},
        "mode": "rlm",
    }
    cfg = Config(data)
    set_config(cfg)

    prompt = build_system_prompt(str(tmp_path))
    assert "ipython" in prompt
    assert "REPL-first" in prompt or "REPL" in prompt


def test_build_system_prompt_uses_default_when_mode_is_tool_first(tmp_path, monkeypatch):
    data = {
        "meta": {"config_version": 13},
        "llm": {
            "default_provider": "openai",
            "default_model": "gpt-4o",
            "default_base_url": "",
            "default_thinking_level": "off",
            "tool_call_idle_timeout_seconds": 180,
            "request_timeout_seconds": 600,
            "auth": {"openai_compat": "auto", "anthropic_compat": "auto"},
            "tls": {"insecure_skip_verify": False},
            "system_prompt": {"git_context": False, "ponytail": False},
        },
        "ui": {
            "theme": "gruvbox-dark",
            "collapse_thinking": True,
            "thinking_lines": "1",
            "colored_tool_badge": True,
            "show_welcome_shortcuts": True,
            "hidden_models": [],
            "model_provider_filter": "",
        },
        "compaction": {"on_overflow": "continue", "threshold_percent": 80},
        "agent": {"max_turns": 500, "default_context_window": 200000},
        "permissions": {"mode": "prompt"},
        "notifications": {"enabled": False, "volume": 0.5},
        "recap": {"enabled": True, "idle_seconds": 60},
        "last_selected": {
            "model_id": None,
            "provider": None,
            "thinking_level": None,
            "agent": None,
        },
        "recent_models": {"entries": []},
        "extensions": [],
        "agents": {"default": "", "switch_mode": "lock", "files": []},
        "task": {"subagent_presets": []},
        "mode": "tool_first",
    }
    cfg = Config(data)
    set_config(cfg)

    prompt = build_system_prompt(str(tmp_path))
    assert "Vtx" in prompt
    assert "ipython" not in prompt


def test_config_schema_accepts_rlm_mode():
    schema = ConfigSchema(
        meta={"config_version": 13},
        llm={
            "default_provider": "openai",
            "default_model": "gpt-4o",
            "default_base_url": "",
            "default_thinking_level": "off",
            "tool_call_idle_timeout_seconds": 180,
            "request_timeout_seconds": 600,
            "auth": {"openai_compat": "auto", "anthropic_compat": "auto"},
            "tls": {"insecure_skip_verify": False},
            "system_prompt": {"git_context": False, "ponytail": False},
        },
        ui={
            "theme": "gruvbox-dark",
            "collapse_thinking": True,
            "thinking_lines": "1",
            "colored_tool_badge": True,
            "show_welcome_shortcuts": True,
            "hidden_models": [],
            "model_provider_filter": "",
        },
        compaction={"on_overflow": "continue", "threshold_percent": 80},
        agent={"max_turns": 500, "default_context_window": 200000},
        permissions={"mode": "prompt"},
        mode="rlm",
    )
    assert schema.mode == "rlm"


def test_config_schema_defaults_to_tool_first():
    schema = ConfigSchema(
        meta={"config_version": 13},
        llm={
            "default_provider": "openai",
            "default_model": "gpt-4o",
            "default_base_url": "",
            "default_thinking_level": "off",
            "tool_call_idle_timeout_seconds": 180,
            "request_timeout_seconds": 600,
            "auth": {"openai_compat": "auto", "anthropic_compat": "auto"},
            "tls": {"insecure_skip_verify": False},
            "system_prompt": {"git_context": False, "ponytail": False},
        },
        ui={
            "theme": "gruvbox-dark",
            "collapse_thinking": True,
            "thinking_lines": "1",
            "colored_tool_badge": True,
            "show_welcome_shortcuts": True,
            "hidden_models": [],
            "model_provider_filter": "",
        },
        compaction={"on_overflow": "continue", "threshold_percent": 80},
        agent={"max_turns": 500, "default_context_window": 200000},
        permissions={"mode": "prompt"},
    )
    assert schema.mode == "tool_first"


def test_rlm_mode_restricts_runtime_tools_to_repl():
    data = {
        "meta": {"config_version": 13},
        "llm": {
            "default_provider": "openai",
            "default_model": "gpt-4o",
            "default_base_url": "",
            "default_thinking_level": "off",
            "tool_call_idle_timeout_seconds": 180,
            "request_timeout_seconds": 600,
            "auth": {"openai_compat": "auto", "anthropic_compat": "auto"},
            "tls": {"insecure_skip_verify": False},
            "system_prompt": {"git_context": False, "ponytail": False},
        },
        "ui": {
            "theme": "gruvbox-dark",
            "collapse_thinking": True,
            "thinking_lines": "1",
            "colored_tool_badge": True,
            "show_welcome_shortcuts": True,
            "hidden_models": [],
            "model_provider_filter": "",
        },
        "compaction": {"on_overflow": "continue", "threshold_percent": 80},
        "agent": {"max_turns": 500, "default_context_window": 200000},
        "permissions": {"mode": "prompt"},
        "notifications": {"enabled": False, "volume": 0.5},
        "recap": {"enabled": True, "idle_seconds": 60},
        "last_selected": {
            "model_id": None,
            "provider": None,
            "thinking_level": None,
            "agent": None,
        },
        "recent_models": {"entries": []},
        "extensions": [],
        "agents": {"default": "", "switch_mode": "lock", "files": []},
        "task": {"subagent_presets": []},
        "mode": "rlm",
    }
    cfg = Config(data)
    set_config(cfg)

    runtime = ConversationRuntime(cwd=str(Path(".")), tools=[])
    runtime._apply_active_agent_to_runtime()
    tool_names = [t.name for t in runtime.tools]
    assert tool_names == ["ipython"]


def test_tool_first_mode_keeps_default_tools():
    data = {
        "meta": {"config_version": 13},
        "llm": {
            "default_provider": "openai",
            "default_model": "gpt-4o",
            "default_base_url": "",
            "default_thinking_level": "off",
            "tool_call_idle_timeout_seconds": 180,
            "request_timeout_seconds": 600,
            "auth": {"openai_compat": "auto", "anthropic_compat": "auto"},
            "tls": {"insecure_skip_verify": False},
            "system_prompt": {"git_context": False, "ponytail": False},
        },
        "ui": {
            "theme": "gruvbox-dark",
            "collapse_thinking": True,
            "thinking_lines": "1",
            "colored_tool_badge": True,
            "show_welcome_shortcuts": True,
            "hidden_models": [],
            "model_provider_filter": "",
        },
        "compaction": {"on_overflow": "continue", "threshold_percent": 80},
        "agent": {"max_turns": 500, "default_context_window": 200000},
        "permissions": {"mode": "prompt"},
        "notifications": {"enabled": False, "volume": 0.5},
        "recap": {"enabled": True, "idle_seconds": 60},
        "last_selected": {
            "model_id": None,
            "provider": None,
            "thinking_level": None,
            "agent": None,
        },
        "recent_models": {"entries": []},
        "extensions": [],
        "agents": {"default": "", "switch_mode": "lock", "files": []},
        "task": {"subagent_presets": []},
        "mode": "tool_first",
    }
    cfg = Config(data)
    set_config(cfg)

    runtime = ConversationRuntime(cwd=str(Path(".")), tools=[])
    runtime._apply_active_agent_to_runtime()
    tool_names = [t.name for t in runtime.tools]
    assert "ipython" in tool_names
    assert len(tool_names) > 1


def test_code_preview_heuristics():
    from vtx.tui.ipython_block import (
        normalize_error_details,
        preview_bash_command,
        preview_ipython_code,
        preview_python_code,
        summarize_error_details,
    )

    # Bash command preview skips set -e and runner wrappers
    lang, text = preview_bash_command("set -e\npytest tests/test_rlm_mode.py")
    assert lang == "bash"
    assert "pytest" in text

    # Python code preview scores mutations and effects higher than imports/definitions
    python_code = """import os
from pathlib import Path

p = Path("test.txt")
p.write_text("hello")
"""
    lang, text = preview_python_code(python_code)
    assert lang == "python"
    assert "write test.txt" in text

    # IPython bash cell magic
    bash_cell = """%%bash
git status
"""
    lang, text = preview_ipython_code(bash_cell)
    assert lang == "bash"
    assert "git status" in text

    # Error normalization and summarization
    traceback_err = """Traceback (most recent call last):
  File "test.py", line 12, in <module>
    raise ValueError("Invalid configuration")
ValueError: Invalid configuration"""
    summary = summarize_error_details(traceback_err)
    assert summary == "ValueError: Invalid configuration"
    assert normalize_error_details("foo\x1b[31mbar\x1b[0m") == "foobar"


def test_rlm_context_data_structure():
    from vtx.ai.agent.ipython_runtime import RLMContext

    ctx = RLMContext(
        session_id="test-session-123",
        cwd="/tmp/test",
        model="gpt-4o",
        system_prompt="system guidance",
        messages=[
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": "run tests now"},
        ],
        tokens={"input_tokens": 100, "output_tokens": 50},
    )

    assert ctx.session_id == "test-session-123"
    assert ctx.cwd == "/tmp/test"
    assert ctx.model == "gpt-4o"
    assert ctx.last_message == {"role": "user", "content": "run tests now"}
    assert ctx.last_user_message == {"role": "user", "content": "run tests now"}

    history = ctx.get_history(limit=2)
    assert len(history) == 2

    search_hits = ctx.search("tests")
    assert len(search_hits) == 1
    assert search_hits[0]["role"] == "user"


@pytest.mark.asyncio
async def test_ipython_runtime_top_level_await(tmp_path):
    from vtx.ai.agent.ipython_manager import IpythonKernel

    kernel = IpythonKernel("test-kernel", cwd=str(tmp_path))
    await kernel.start()
    try:
        code = """import asyncio
await asyncio.sleep(0.01)
x = 42
x"""
        output, errored = await kernel.execute(code, timeout=5.0)
        assert not errored
        assert "42" in output
    finally:
        await kernel.close()


@pytest.mark.asyncio
async def test_ipython_runtime_code_as_variable(tmp_path):
    from vtx.ai.agent.ipython_manager import IpythonKernel

    kernel = IpythonKernel("test-kernel-code-vars", cwd=str(tmp_path))
    await kernel.start()
    try:
        # First cell defines a computation
        cell1 = """a = 100
b = 20
a + b"""
        out1, err1 = await kernel.execute(cell1, timeout=5.0)
        assert not err1
        assert "120" in out1

        # Second cell accesses In, Out, _i, _
        cell2 = """prev_code = In[1]
prev_res = Out[1]
f"code={prev_code.splitlines()[-1]}; res={prev_res}; under={_}"
"""
        out2, err2 = await kernel.execute(cell2, timeout=5.0)
        assert not err2
        assert "a + b" in out2
        assert "120" in out2

        # Third cell uses context.code_history and rerun
        cell3 = """rerun(1)"""
        out3, err3 = await kernel.execute(cell3, timeout=5.0)
        assert not err3
        assert "120" in out3

        # Fourth cell uses ! shell escape
        cell4 = """!echo 'hello from bang'"""
        out4, err4 = await kernel.execute(cell4, timeout=5.0)
        assert not err4
        assert "hello from bang" in out4

        # Fifth cell uses %%bash cell magic
        cell5 = """%%bash
echo 'hello from magic'
"""
        out5, err5 = await kernel.execute(cell5, timeout=5.0)
        assert not err5
        assert "hello from magic" in out5
    finally:
        await kernel.close()


@pytest.mark.asyncio
async def test_ipython_runtime_out_eviction(tmp_path):
    """Out dict should not grow beyond _MAX_OUT_ENTRIES."""
    from vtx.ai.agent.ipython_manager import IpythonKernel

    kernel = IpythonKernel("test-kernel-out-eviction", cwd=str(tmp_path))
    await kernel.start()
    try:
        # Execute many cells to trigger eviction
        for i in range(1050):
            cell = f"x = {i}"
            _out, err = await kernel.execute(cell, timeout=5.0)
            assert not err

        # Check Out size through the kernel itself (subprocess has separate namespace)
        out_size, err = await kernel.execute("len(Out)", timeout=5.0)
        assert not err
        assert int(out_size.strip()) <= 1000

        # Check that oldest entries were evicted by verifying a recent value exists
        out_val, err = await kernel.execute("Out[1050]", timeout=5.0)
        assert not err
        assert "1050" in out_val
    finally:
        await kernel.close()
