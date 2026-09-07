"""Tests for RLM mode configuration, prompt assembly, and tool filtering."""

from __future__ import annotations

from pathlib import Path

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
