"""Lazy re-export barrels: every public name must still resolve to the same object.

``ctx/__init__.py``, ``ctx.core``, ``ctx.ai``, and ``ctx.agent`` re-export their
public surfaces lazily so that importing any ``ctx.*`` submodule does not drag a
whole unrelated import graph into the process. The tables below record which
source module each exported name came from when the barrels were eager, so this
test fails if a name silently starts resolving to a different object.
"""

import subprocess

import pytest

# name -> (source module, attribute) it was bound from when the barrel was eager
VX_SOURCES = [
    (
        "ctx",
        [
            ("Agent", "ctx.agent.sdk", "Agent"),
            ("AllowlistApprove", "ctx.agent.sdk", "AllowlistApprove"),
            ("AutoApprove", "ctx.agent.sdk", "AutoApprove"),
            ("ConsoleTraceProcessor", "ctx.agent.sdk", "ConsoleTraceProcessor"),
            ("FunctionTool", "ctx.agent.sdk", "FunctionTool"),
            ("GuardrailFunctionOutput", "ctx.agent.sdk", "GuardrailFunctionOutput"),
            ("Handoff", "ctx.agent.sdk", "Handoff"),
            ("HandoffCallItem", "ctx.agent.sdk", "HandoffCallItem"),
            ("HandoffInputData", "ctx.agent.sdk", "HandoffInputData"),
            ("HandoffOutputItem", "ctx.agent.sdk", "HandoffOutputItem"),
            ("InMemorySession", "ctx.agent.sdk", "InMemorySession"),
            ("InputGuardrail", "ctx.agent.sdk", "InputGuardrail"),
            (
                "InputGuardrailTripwireTriggered",
                "ctx.agent.sdk",
                "InputGuardrailTripwireTriggered",
            ),
            ("JSONLSession", "ctx.agent.sdk", "JSONLSession"),
            ("JSONLTraceProcessor", "ctx.agent.sdk", "JSONLTraceProcessor"),
            ("MessageOutputItem", "ctx.agent.sdk", "MessageOutputItem"),
            ("OutputGuardrail", "ctx.agent.sdk", "OutputGuardrail"),
            (
                "OutputGuardrailTripwireTriggered",
                "ctx.agent.sdk",
                "OutputGuardrailTripwireTriggered",
            ),
            ("PermissionDecision", "ctx.agent.sdk", "PermissionDecision"),
            ("PermissionPolicy", "ctx.agent.sdk", "PermissionPolicy"),
            ("PromptApprove", "ctx.agent.sdk", "PromptApprove"),
            ("ReasoningItem", "ctx.agent.sdk", "ReasoningItem"),
            ("RunConfig", "ctx.agent.sdk", "RunConfig"),
            ("RunItem", "ctx.agent.sdk", "RunItem"),
            ("RunResult", "ctx.agent.sdk", "RunResult"),
            ("RunStreamed", "ctx.agent.sdk", "RunStreamed"),
            ("Runner", "ctx.agent.sdk", "Runner"),
            ("Session", "ctx.agent.sdk", "Session"),
            ("SessionSettings", "ctx.agent.sdk", "SessionSettings"),
            ("Span", "ctx.agent.sdk", "Span"),
            ("ToolApprovalItem", "ctx.agent.sdk", "ToolApprovalItem"),
            ("ToolCallItem", "ctx.agent.sdk", "ToolCallItem"),
            ("ToolCallOutputItem", "ctx.agent.sdk", "ToolCallOutputItem"),
            ("ToolGuardrailFunctionOutput", "ctx.agent.sdk", "ToolGuardrailFunctionOutput"),
            (
                "ToolInputGuardrailTripwireTriggered",
                "ctx.agent.sdk",
                "ToolInputGuardrailTripwireTriggered",
            ),
            (
                "ToolOutputGuardrailTripwireTriggered",
                "ctx.agent.sdk",
                "ToolOutputGuardrailTripwireTriggered",
            ),
            ("Trace", "ctx.agent.sdk", "Trace"),
            ("TraceProcessor", "ctx.agent.sdk", "TraceProcessor"),
            ("Usage", "ctx.agent.sdk", "Usage"),
            ("_ToolApprovalItem", "ctx.agent.sdk", "_ToolApprovalItem"),
            ("add_trace_processor", "ctx.agent.sdk", "add_trace_processor"),
            ("disable_tracing", "ctx.agent.sdk", "disable_tracing"),
            ("enable_tracing", "ctx.agent.sdk", "enable_tracing"),
            ("handoff", "ctx.agent.sdk", "handoff"),
            ("input_guardrail", "ctx.agent.sdk", "input_guardrail"),
            ("output_guardrail", "ctx.agent.sdk", "output_guardrail"),
            ("span", "ctx.agent.sdk", "span"),
            ("tool", "ctx.agent.sdk", "tool"),
            ("tool_input_guardrail", "ctx.agent.sdk", "tool_input_guardrail"),
            ("tool_output_guardrail", "ctx.agent.sdk", "tool_output_guardrail"),
            ("trace", "ctx.agent.sdk", "trace"),
            ("AVAILABLE_BINARIES", "ctx.coding_agent", "AVAILABLE_BINARIES"),
            ("CONFIG_DIR_NAME", "ctx.coding_agent", "CONFIG_DIR_NAME"),
            ("CURRENT_CONFIG_VERSION", "ctx.coding_agent", "CURRENT_CONFIG_VERSION"),
            ("PACKAGE_NAME", "ctx.coding_agent", "PACKAGE_NAME"),
            ("VERSION", "ctx.coding_agent", "VERSION"),
            ("_exit_code", "ctx.coding_agent", "_exit_code"),
            ("add_recent_model", "ctx.coding_agent", "add_recent_model"),
            ("config", "ctx.coding_agent", "config"),
            ("consume_config_warnings", "ctx.coding_agent", "consume_config_warnings"),
            ("format_version", "ctx.coding_agent", "format_version"),
            ("get_agents_dir", "ctx.coding_agent", "get_agents_dir"),
            ("get_config", "ctx.coding_agent", "get_config"),
            ("get_config_dir", "ctx.coding_agent", "get_config_dir"),
            ("get_last_selected", "ctx.coding_agent", "get_last_selected"),
            ("get_recent_models", "ctx.coding_agent", "get_recent_models"),
            ("reload_config", "ctx.coding_agent", "reload_config"),
            ("render_run", "ctx.coding_agent", "render_run"),
            ("reset_config", "ctx.coding_agent", "reset_config"),
            ("resolve_prompt", "ctx.coding_agent", "resolve_prompt"),
            ("run_headless", "ctx.coding_agent", "run_headless"),
            ("set_colored_tool_badge", "ctx.coding_agent", "set_colored_tool_badge"),
            ("set_config", "ctx.coding_agent", "set_config"),
            ("set_git_context", "ctx.coding_agent", "set_git_context"),
            ("set_last_selected", "ctx.coding_agent", "set_last_selected"),
            ("set_model_provider_filter", "ctx.coding_agent", "set_model_provider_filter"),
            ("set_notifications_enabled", "ctx.coding_agent", "set_notifications_enabled"),
            ("set_permissions_mode", "ctx.coding_agent", "set_permissions_mode"),
            ("set_show_welcome_shortcuts", "ctx.coding_agent", "set_show_welcome_shortcuts"),
            ("set_theme", "ctx.coding_agent", "set_theme"),
            ("set_thinking_lines", "ctx.coding_agent", "set_thinking_lines"),
            ("update_available_binaries", "ctx.coding_agent", "update_available_binaries"),
            ("Message", "ctx.protocol", "Message"),
        ],
    ),
    (
        "ctx.core",
        [
            ("AgentEndEvent", "ctx.core.events", "AgentEndEvent"),
            ("AgentStartEvent", "ctx.core.events", "AgentStartEvent"),
            ("AskUserEvent", "ctx.core.events", "AskUserEvent"),
            ("BackgroundTaskCompletedEvent", "ctx.core.events", "BackgroundTaskCompletedEvent"),
            ("CompactionEndEvent", "ctx.core.events", "CompactionEndEvent"),
            ("CompactionProgressEvent", "ctx.core.events", "CompactionProgressEvent"),
            ("CompactionStartEvent", "ctx.core.events", "CompactionStartEvent"),
            ("ErrorEvent", "ctx.core.events", "ErrorEvent"),
            ("HostNoticeEvent", "ctx.core.events", "HostNoticeEvent"),
            ("InterruptedEvent", "ctx.core.events", "InterruptedEvent"),
            ("RetryEvent", "ctx.core.events", "RetryEvent"),
            ("SessionEndEvent", "ctx.core.events", "SessionEndEvent"),
            ("SessionStartEvent", "ctx.core.events", "SessionStartEvent"),
            ("TextDeltaEvent", "ctx.core.events", "TextDeltaEvent"),
            ("TextEndEvent", "ctx.core.events", "TextEndEvent"),
            ("TextStartEvent", "ctx.core.events", "TextStartEvent"),
            ("ThinkingDeltaEvent", "ctx.core.events", "ThinkingDeltaEvent"),
            ("ThinkingEndEvent", "ctx.core.events", "ThinkingEndEvent"),
            ("ThinkingStartEvent", "ctx.core.events", "ThinkingStartEvent"),
            ("ToolApprovalEvent", "ctx.core.events", "ToolApprovalEvent"),
            ("ToolArgsDeltaEvent", "ctx.core.events", "ToolArgsDeltaEvent"),
            ("ToolArgsTokenUpdateEvent", "ctx.core.events", "ToolArgsTokenUpdateEvent"),
            ("ToolEndEvent", "ctx.core.events", "ToolEndEvent"),
            ("ToolOutputDeltaEvent", "ctx.core.events", "ToolOutputDeltaEvent"),
            ("ToolResultEvent", "ctx.core.events", "ToolResultEvent"),
            ("ToolStartEvent", "ctx.core.events", "ToolStartEvent"),
            ("TurnEndEvent", "ctx.core.events", "TurnEndEvent"),
            ("TurnStartEvent", "ctx.core.events", "TurnStartEvent"),
            ("WarningEvent", "ctx.core.events", "WarningEvent"),
            ("ApprovalResponse", "ctx.core.permissions", "ApprovalResponse"),
            ("AskUserAnswer", "ctx.core.permissions", "AskUserAnswer"),
            ("AskUserOption", "ctx.core.permissions", "AskUserOption"),
            ("AskUserQuestion", "ctx.core.permissions", "AskUserQuestion"),
            ("AskUserResponse", "ctx.core.permissions", "AskUserResponse"),
            ("NotificationEvent", "ctx.core.notify", "NotificationEvent"),
            ("notify", "ctx.core.notify", "notify"),
            ("PermissionDecision", "ctx.core.permissions", "PermissionDecision"),
            ("_is_safe_bash_command", "ctx.core.permissions", "_is_safe_bash_command"),
            ("check_permission", "ctx.core.permissions", "check_permission"),
            ("get_scratchpad_dir", "ctx.core.scratchpad", "get_scratchpad_dir"),
            ("init_scratchpad", "ctx.core.scratchpad", "init_scratchpad"),
            ("is_scratchpad_path", "ctx.core.scratchpad", "is_scratchpad_path"),
        ],
    ),
    (
        "ctx.ai",
        [
            ("DEFAULT_THINKING_LEVELS", "ctx.ai.base", "DEFAULT_THINKING_LEVELS"),
            ("BaseProvider", "ctx.ai.base", "BaseProvider"),
            ("LLMStream", "ctx.ai.base", "LLMStream"),
            ("ProviderConfig", "ctx.ai.base", "ProviderConfig"),
            ("ApiType", "ctx.ai.models", "ApiType"),
            ("Model", "ctx.ai.models", "Model"),
            ("get_all_models", "ctx.ai.models", "get_all_models"),
            ("get_max_tokens", "ctx.ai.models", "get_max_tokens"),
            ("get_model", "ctx.ai.models", "get_model"),
            ("get_models_by_provider", "ctx.ai.models", "get_models_by_provider"),
            ("DYNAMIC_PROVIDERS", "ctx.ai.dynamic_models", "DYNAMIC_PROVIDERS"),
            ("DynamicModelEntry", "ctx.ai.dynamic_models", "DynamicModelEntry"),
            ("DynamicProviderConfig", "ctx.ai.dynamic_models", "DynamicProviderConfig"),
            ("find_dynamic_model", "ctx.ai.dynamic_models", "find_dynamic_model"),
            (
                "get_all_models_with_dynamic",
                "ctx.ai.dynamic_models",
                "get_all_models_with_dynamic",
            ),
            ("get_dynamic_models", "ctx.ai.dynamic_models", "get_dynamic_models"),
            ("get_dynamic_provider", "ctx.ai.dynamic_models", "get_dynamic_provider"),
            (
                "get_dynamic_provider_headers",
                "ctx.ai.dynamic_models",
                "get_dynamic_provider_headers",
            ),
            ("get_provider_models", "ctx.ai.dynamic_models", "get_provider_models"),
            ("refresh_all_providers", "ctx.ai.dynamic_models", "refresh_all_providers"),
            ("refresh_provider", "ctx.ai.dynamic_models", "refresh_provider"),
            ("register_dynamic_provider", "ctx.ai.dynamic_models", "register_dynamic_provider"),
            ("CodexCredentials", "ctx.ai.oauth", "CodexCredentials"),
            ("clear_api_key", "ctx.ai.oauth", "clear_api_key"),
            ("clear_cline_credentials", "ctx.ai.oauth", "clear_cline_credentials"),
            ("clear_codex_credentials", "ctx.ai.oauth", "clear_codex_credentials"),
            ("cline_login", "ctx.ai.oauth", "cline_login"),
            ("codex_login", "ctx.ai.oauth", "codex_login"),
            ("get_codex_auth_path", "ctx.ai.oauth", "get_codex_auth_path"),
            ("get_dynamic_api_key", "ctx.ai.oauth", "get_dynamic_api_key"),
            ("get_provider_status", "ctx.ai.oauth", "get_provider_status"),
            ("get_valid_cline_credentials", "ctx.ai.oauth", "get_valid_cline_credentials"),
            ("get_valid_cline_token", "ctx.ai.oauth", "get_valid_cline_token"),
            ("get_valid_cline_token_sync", "ctx.ai.oauth", "get_valid_cline_token_sync"),
            ("get_valid_codex_credentials", "ctx.ai.oauth", "get_valid_codex_credentials"),
            ("get_valid_codex_token", "ctx.ai.oauth", "get_valid_codex_token"),
            ("get_valid_codex_token_sync", "ctx.ai.oauth", "get_valid_codex_token_sync"),
            ("get_valid_copilot_token_sync", "ctx.ai.oauth", "get_valid_copilot_token_sync"),
            ("get_valid_token", "ctx.ai.oauth", "get_valid_token"),
            ("has_api_key", "ctx.ai.oauth", "has_api_key"),
            ("is_cline_logged_in", "ctx.ai.oauth", "is_cline_logged_in"),
            ("is_codex_logged_in", "ctx.ai.oauth", "is_codex_logged_in"),
            ("is_copilot_logged_in", "ctx.ai.oauth", "is_copilot_logged_in"),
            ("is_devin_logged_in", "ctx.ai.oauth", "is_devin_logged_in"),
            ("load_api_key", "ctx.ai.oauth", "load_api_key"),
            ("load_cline_credentials", "ctx.ai.oauth", "load_cline_credentials"),
            ("load_codex_credentials", "ctx.ai.oauth", "load_codex_credentials"),
            ("login", "ctx.ai.oauth", "login"),
            ("refresh_cline_token", "ctx.ai.oauth", "refresh_cline_token"),
            ("refresh_codex_token", "ctx.ai.oauth", "refresh_codex_token"),
            ("save_api_key", "ctx.ai.oauth", "save_api_key"),
            ("save_codex_credentials", "ctx.ai.oauth", "save_codex_credentials"),
            ("clear_copilot_credentials", "ctx.ai.oauth", "clear_credentials"),
            ("get_copilot_token", "ctx.ai.oauth", "get_valid_token"),
            ("load_copilot_credentials", "ctx.ai.oauth", "load_credentials"),
            ("copilot_login", "ctx.ai.oauth", "login"),
            ("ProviderInfo", "ctx.ai.provider_catalog", "ProviderInfo"),
            ("detect_provider_from_env", "ctx.ai.provider_catalog", "detect_provider_from_env"),
            ("list_providers", "ctx.ai.provider_catalog", "list_providers"),
            ("get_provider_info", "ctx.ai.provider_catalog", "get"),
            ("get_provider_class", "ctx.ai.providers", "get_provider_class"),
            ("resolve_provider_api_type", "ctx.ai.providers", "resolve_provider_api_type"),
        ],
    ),
    (
        "ctx.agent",
        [
            ("CommandOutcome", "ctx.plugins.api", "CommandOutcome"),
            ("ExtensionAPI", "ctx.plugins.api", "ExtensionAPI"),
            ("ExtensionCommand", "ctx.plugins.api", "ExtensionCommand"),
            ("ExtensionRecord", "ctx.plugins.api", "ExtensionRecord"),
            ("ExtensionTool", "ctx.plugins.api", "ExtensionTool"),
            ("AGENT_CHANGED", "ctx.plugins.event_contract", "AGENT_CHANGED"),
            ("AGENT_START", "ctx.plugins.event_contract", "AGENT_START"),
            ("LoadedExtensions", "ctx.plugins.extensions", "LoadedExtensions"),
            ("load_for_runtime", "ctx.plugins.extensions", "load_for_runtime"),
        ],
    ),
]


def _source_object(module_name: str, attribute: str) -> object:
    import importlib

    return getattr(importlib.import_module(module_name), attribute)


@pytest.mark.parametrize("barrel", [b[0] for b in VX_SOURCES])
def test_all_names_resolve_to_original_object(barrel: str) -> None:
    import importlib

    module = importlib.import_module(barrel)
    expected = dict(VX_SOURCES)[barrel]

    assert module.__all__, f"{barrel} has an empty __all__"
    assert not set(module.__all__) - {name for name, _, _ in expected}, (
        f"{barrel} exports a name with no recorded source"
    )

    for exported_name, source_module, attribute in expected:
        assert getattr(module, exported_name) is _source_object(source_module, attribute), (
            f"{barrel}.{exported_name} no longer resolves to {source_module}.{attribute}"
        )


@pytest.mark.parametrize("barrel", [b[0] for b in VX_SOURCES])
def test_unknown_attribute_raises(barrel: str) -> None:
    import importlib

    module = importlib.import_module(barrel)
    unknown = "definitely_not_a_public_name"
    try:
        value = getattr(module, unknown)
    except AttributeError as exc:
        assert unknown in str(exc) and barrel in str(exc)
    else:
        raise AssertionError(f"{barrel}.{unknown} unexpectedly resolved to {value!r}")


@pytest.mark.parametrize("barrel", [b[0] for b in VX_SOURCES])
def test_dir_lists_exports(barrel: str) -> None:
    import importlib

    module = importlib.import_module(barrel)
    assert dir(module) == sorted(module.__all__)


@pytest.mark.parametrize("barrel", [b[0] for b in VX_SOURCES])
def test_public_surface_unchanged_by_laziness(barrel: str) -> None:
    """``__all__`` must stay byte-identical to the eager barrels."""
    import re

    head = f"src/{barrel.replace('.', '/')}/__init__.py"
    with open(head, encoding="utf-8") as handle:
        body = handle.read()
    exported = re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', body.split("__all__ = [")[1])

    old = subprocess.run(
        ["git", "show", f"HEAD:{head}"], capture_output=True, text=True, check=True
    ).stdout
    original = re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', old.split("__all__ = [")[1])

    assert exported == original, f"{head} __all__ changed from the eager version"
