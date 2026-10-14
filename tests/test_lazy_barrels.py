"""Lazy re-export barrels: every public name must still resolve to the same object.

``vtx/__init__.py``, ``vtx.core``, ``vtx.ai``, and ``vtx.agent`` re-export their
public surfaces lazily so that importing any ``vtx.*`` submodule does not drag a
whole unrelated import graph into the process. The tables below record which
source module each exported name came from when the barrels were eager, so this
test fails if a name silently starts resolving to a different object.
"""

import subprocess

import pytest

# name -> (source module, attribute) it was bound from when the barrel was eager
VX_SOURCES = [
    (
        "vtx",
        [
            ("Agent", "vtx.agent.sdk", "Agent"),
            ("AllowlistApprove", "vtx.agent.sdk", "AllowlistApprove"),
            ("AutoApprove", "vtx.agent.sdk", "AutoApprove"),
            ("ConsoleTraceProcessor", "vtx.agent.sdk", "ConsoleTraceProcessor"),
            ("FunctionTool", "vtx.agent.sdk", "FunctionTool"),
            ("GuardrailFunctionOutput", "vtx.agent.sdk", "GuardrailFunctionOutput"),
            ("Handoff", "vtx.agent.sdk", "Handoff"),
            ("HandoffCallItem", "vtx.agent.sdk", "HandoffCallItem"),
            ("HandoffInputData", "vtx.agent.sdk", "HandoffInputData"),
            ("HandoffOutputItem", "vtx.agent.sdk", "HandoffOutputItem"),
            ("InMemorySession", "vtx.agent.sdk", "InMemorySession"),
            ("InputGuardrail", "vtx.agent.sdk", "InputGuardrail"),
            (
                "InputGuardrailTripwireTriggered",
                "vtx.agent.sdk",
                "InputGuardrailTripwireTriggered",
            ),
            ("JSONLSession", "vtx.agent.sdk", "JSONLSession"),
            ("JSONLTraceProcessor", "vtx.agent.sdk", "JSONLTraceProcessor"),
            ("MessageOutputItem", "vtx.agent.sdk", "MessageOutputItem"),
            ("OutputGuardrail", "vtx.agent.sdk", "OutputGuardrail"),
            (
                "OutputGuardrailTripwireTriggered",
                "vtx.agent.sdk",
                "OutputGuardrailTripwireTriggered",
            ),
            ("PermissionDecision", "vtx.agent.sdk", "PermissionDecision"),
            ("PermissionPolicy", "vtx.agent.sdk", "PermissionPolicy"),
            ("PromptApprove", "vtx.agent.sdk", "PromptApprove"),
            ("ReasoningItem", "vtx.agent.sdk", "ReasoningItem"),
            ("RunConfig", "vtx.agent.sdk", "RunConfig"),
            ("RunItem", "vtx.agent.sdk", "RunItem"),
            ("RunResult", "vtx.agent.sdk", "RunResult"),
            ("RunStreamed", "vtx.agent.sdk", "RunStreamed"),
            ("Runner", "vtx.agent.sdk", "Runner"),
            ("Session", "vtx.agent.sdk", "Session"),
            ("SessionSettings", "vtx.agent.sdk", "SessionSettings"),
            ("Span", "vtx.agent.sdk", "Span"),
            ("ToolApprovalItem", "vtx.agent.sdk", "ToolApprovalItem"),
            ("ToolCallItem", "vtx.agent.sdk", "ToolCallItem"),
            ("ToolCallOutputItem", "vtx.agent.sdk", "ToolCallOutputItem"),
            ("ToolGuardrailFunctionOutput", "vtx.agent.sdk", "ToolGuardrailFunctionOutput"),
            (
                "ToolInputGuardrailTripwireTriggered",
                "vtx.agent.sdk",
                "ToolInputGuardrailTripwireTriggered",
            ),
            (
                "ToolOutputGuardrailTripwireTriggered",
                "vtx.agent.sdk",
                "ToolOutputGuardrailTripwireTriggered",
            ),
            ("Trace", "vtx.agent.sdk", "Trace"),
            ("TraceProcessor", "vtx.agent.sdk", "TraceProcessor"),
            ("Usage", "vtx.agent.sdk", "Usage"),
            ("_ToolApprovalItem", "vtx.agent.sdk", "_ToolApprovalItem"),
            ("add_trace_processor", "vtx.agent.sdk", "add_trace_processor"),
            ("disable_tracing", "vtx.agent.sdk", "disable_tracing"),
            ("enable_tracing", "vtx.agent.sdk", "enable_tracing"),
            ("handoff", "vtx.agent.sdk", "handoff"),
            ("input_guardrail", "vtx.agent.sdk", "input_guardrail"),
            ("output_guardrail", "vtx.agent.sdk", "output_guardrail"),
            ("span", "vtx.agent.sdk", "span"),
            ("tool", "vtx.agent.sdk", "tool"),
            ("tool_input_guardrail", "vtx.agent.sdk", "tool_input_guardrail"),
            ("tool_output_guardrail", "vtx.agent.sdk", "tool_output_guardrail"),
            ("trace", "vtx.agent.sdk", "trace"),
            ("AVAILABLE_BINARIES", "vtx.coding_agent", "AVAILABLE_BINARIES"),
            ("CONFIG_DIR_NAME", "vtx.coding_agent", "CONFIG_DIR_NAME"),
            ("CURRENT_CONFIG_VERSION", "vtx.coding_agent", "CURRENT_CONFIG_VERSION"),
            ("PACKAGE_NAME", "vtx.coding_agent", "PACKAGE_NAME"),
            ("VERSION", "vtx.coding_agent", "VERSION"),
            ("_exit_code", "vtx.coding_agent", "_exit_code"),
            ("add_recent_model", "vtx.coding_agent", "add_recent_model"),
            ("config", "vtx.coding_agent", "config"),
            ("consume_config_warnings", "vtx.coding_agent", "consume_config_warnings"),
            ("format_version", "vtx.coding_agent", "format_version"),
            ("get_agents_dir", "vtx.coding_agent", "get_agents_dir"),
            ("get_config", "vtx.coding_agent", "get_config"),
            ("get_config_dir", "vtx.coding_agent", "get_config_dir"),
            ("get_last_selected", "vtx.coding_agent", "get_last_selected"),
            ("get_recent_models", "vtx.coding_agent", "get_recent_models"),
            ("reload_config", "vtx.coding_agent", "reload_config"),
            ("render_run", "vtx.coding_agent", "render_run"),
            ("reset_config", "vtx.coding_agent", "reset_config"),
            ("resolve_prompt", "vtx.coding_agent", "resolve_prompt"),
            ("run_headless", "vtx.coding_agent", "run_headless"),
            ("set_colored_tool_badge", "vtx.coding_agent", "set_colored_tool_badge"),
            ("set_config", "vtx.coding_agent", "set_config"),
            ("set_git_context", "vtx.coding_agent", "set_git_context"),
            ("set_last_selected", "vtx.coding_agent", "set_last_selected"),
            ("set_model_provider_filter", "vtx.coding_agent", "set_model_provider_filter"),
            ("set_notifications_enabled", "vtx.coding_agent", "set_notifications_enabled"),
            ("set_permissions_mode", "vtx.coding_agent", "set_permissions_mode"),
            ("set_show_welcome_shortcuts", "vtx.coding_agent", "set_show_welcome_shortcuts"),
            ("set_theme", "vtx.coding_agent", "set_theme"),
            ("set_thinking_lines", "vtx.coding_agent", "set_thinking_lines"),
            ("update_available_binaries", "vtx.coding_agent", "update_available_binaries"),
            ("Message", "vtx.protocol", "Message"),
        ],
    ),
    (
        "vtx.core",
        [
            ("AgentEndEvent", "vtx.core.events", "AgentEndEvent"),
            ("AgentStartEvent", "vtx.core.events", "AgentStartEvent"),
            ("AskUserEvent", "vtx.core.events", "AskUserEvent"),
            ("BackgroundTaskCompletedEvent", "vtx.core.events", "BackgroundTaskCompletedEvent"),
            ("CompactionEndEvent", "vtx.core.events", "CompactionEndEvent"),
            ("CompactionProgressEvent", "vtx.core.events", "CompactionProgressEvent"),
            ("CompactionStartEvent", "vtx.core.events", "CompactionStartEvent"),
            ("ErrorEvent", "vtx.core.events", "ErrorEvent"),
            ("HostNoticeEvent", "vtx.core.events", "HostNoticeEvent"),
            ("InterruptedEvent", "vtx.core.events", "InterruptedEvent"),
            ("RetryEvent", "vtx.core.events", "RetryEvent"),
            ("SessionEndEvent", "vtx.core.events", "SessionEndEvent"),
            ("SessionStartEvent", "vtx.core.events", "SessionStartEvent"),
            ("TextDeltaEvent", "vtx.core.events", "TextDeltaEvent"),
            ("TextEndEvent", "vtx.core.events", "TextEndEvent"),
            ("TextStartEvent", "vtx.core.events", "TextStartEvent"),
            ("ThinkingDeltaEvent", "vtx.core.events", "ThinkingDeltaEvent"),
            ("ThinkingEndEvent", "vtx.core.events", "ThinkingEndEvent"),
            ("ThinkingStartEvent", "vtx.core.events", "ThinkingStartEvent"),
            ("ToolApprovalEvent", "vtx.core.events", "ToolApprovalEvent"),
            ("ToolArgsDeltaEvent", "vtx.core.events", "ToolArgsDeltaEvent"),
            ("ToolArgsTokenUpdateEvent", "vtx.core.events", "ToolArgsTokenUpdateEvent"),
            ("ToolEndEvent", "vtx.core.events", "ToolEndEvent"),
            ("ToolOutputDeltaEvent", "vtx.core.events", "ToolOutputDeltaEvent"),
            ("ToolResultEvent", "vtx.core.events", "ToolResultEvent"),
            ("ToolStartEvent", "vtx.core.events", "ToolStartEvent"),
            ("TurnEndEvent", "vtx.core.events", "TurnEndEvent"),
            ("TurnStartEvent", "vtx.core.events", "TurnStartEvent"),
            ("WarningEvent", "vtx.core.events", "WarningEvent"),
            ("ApprovalResponse", "vtx.core.permissions", "ApprovalResponse"),
            ("AskUserAnswer", "vtx.core.permissions", "AskUserAnswer"),
            ("AskUserOption", "vtx.core.permissions", "AskUserOption"),
            ("AskUserQuestion", "vtx.core.permissions", "AskUserQuestion"),
            ("AskUserResponse", "vtx.core.permissions", "AskUserResponse"),
            ("NotificationEvent", "vtx.core.notify", "NotificationEvent"),
            ("notify", "vtx.core.notify", "notify"),
            ("PermissionDecision", "vtx.core.permissions", "PermissionDecision"),
            ("_is_safe_bash_command", "vtx.core.permissions", "_is_safe_bash_command"),
            ("check_permission", "vtx.core.permissions", "check_permission"),
            ("get_scratchpad_dir", "vtx.core.scratchpad", "get_scratchpad_dir"),
            ("init_scratchpad", "vtx.core.scratchpad", "init_scratchpad"),
            ("is_scratchpad_path", "vtx.core.scratchpad", "is_scratchpad_path"),
        ],
    ),
    (
        "vtx.ai",
        [
            ("DEFAULT_THINKING_LEVELS", "vtx.ai.base", "DEFAULT_THINKING_LEVELS"),
            ("BaseProvider", "vtx.ai.base", "BaseProvider"),
            ("LLMStream", "vtx.ai.base", "LLMStream"),
            ("ProviderConfig", "vtx.ai.base", "ProviderConfig"),
            ("ApiType", "vtx.ai.models", "ApiType"),
            ("Model", "vtx.ai.models", "Model"),
            ("get_all_models", "vtx.ai.models", "get_all_models"),
            ("get_max_tokens", "vtx.ai.models", "get_max_tokens"),
            ("get_model", "vtx.ai.models", "get_model"),
            ("get_models_by_provider", "vtx.ai.models", "get_models_by_provider"),
            ("DYNAMIC_PROVIDERS", "vtx.ai.dynamic_models", "DYNAMIC_PROVIDERS"),
            ("DynamicModelEntry", "vtx.ai.dynamic_models", "DynamicModelEntry"),
            ("DynamicProviderConfig", "vtx.ai.dynamic_models", "DynamicProviderConfig"),
            ("find_dynamic_model", "vtx.ai.dynamic_models", "find_dynamic_model"),
            (
                "get_all_models_with_dynamic",
                "vtx.ai.dynamic_models",
                "get_all_models_with_dynamic",
            ),
            ("get_dynamic_models", "vtx.ai.dynamic_models", "get_dynamic_models"),
            ("get_dynamic_provider", "vtx.ai.dynamic_models", "get_dynamic_provider"),
            (
                "get_dynamic_provider_headers",
                "vtx.ai.dynamic_models",
                "get_dynamic_provider_headers",
            ),
            ("get_provider_models", "vtx.ai.dynamic_models", "get_provider_models"),
            ("refresh_all_providers", "vtx.ai.dynamic_models", "refresh_all_providers"),
            ("refresh_provider", "vtx.ai.dynamic_models", "refresh_provider"),
            ("register_dynamic_provider", "vtx.ai.dynamic_models", "register_dynamic_provider"),
            ("CodexCredentials", "vtx.ai.oauth", "CodexCredentials"),
            ("clear_api_key", "vtx.ai.oauth", "clear_api_key"),
            ("clear_cline_credentials", "vtx.ai.oauth", "clear_cline_credentials"),
            ("clear_codex_credentials", "vtx.ai.oauth", "clear_codex_credentials"),
            ("cline_login", "vtx.ai.oauth", "cline_login"),
            ("codex_login", "vtx.ai.oauth", "codex_login"),
            ("get_codex_auth_path", "vtx.ai.oauth", "get_codex_auth_path"),
            ("get_dynamic_api_key", "vtx.ai.oauth", "get_dynamic_api_key"),
            ("get_provider_status", "vtx.ai.oauth", "get_provider_status"),
            ("get_valid_cline_credentials", "vtx.ai.oauth", "get_valid_cline_credentials"),
            ("get_valid_cline_token", "vtx.ai.oauth", "get_valid_cline_token"),
            ("get_valid_cline_token_sync", "vtx.ai.oauth", "get_valid_cline_token_sync"),
            ("get_valid_codex_credentials", "vtx.ai.oauth", "get_valid_codex_credentials"),
            ("get_valid_codex_token", "vtx.ai.oauth", "get_valid_codex_token"),
            ("get_valid_codex_token_sync", "vtx.ai.oauth", "get_valid_codex_token_sync"),
            ("get_valid_copilot_token_sync", "vtx.ai.oauth", "get_valid_copilot_token_sync"),
            ("get_valid_token", "vtx.ai.oauth", "get_valid_token"),
            ("has_api_key", "vtx.ai.oauth", "has_api_key"),
            ("is_cline_logged_in", "vtx.ai.oauth", "is_cline_logged_in"),
            ("is_codex_logged_in", "vtx.ai.oauth", "is_codex_logged_in"),
            ("is_copilot_logged_in", "vtx.ai.oauth", "is_copilot_logged_in"),
            ("load_api_key", "vtx.ai.oauth", "load_api_key"),
            ("load_cline_credentials", "vtx.ai.oauth", "load_cline_credentials"),
            ("load_codex_credentials", "vtx.ai.oauth", "load_codex_credentials"),
            ("login", "vtx.ai.oauth", "login"),
            ("refresh_cline_token", "vtx.ai.oauth", "refresh_cline_token"),
            ("refresh_codex_token", "vtx.ai.oauth", "refresh_codex_token"),
            ("save_api_key", "vtx.ai.oauth", "save_api_key"),
            ("save_codex_credentials", "vtx.ai.oauth", "save_codex_credentials"),
            ("clear_copilot_credentials", "vtx.ai.oauth", "clear_credentials"),
            ("get_copilot_token", "vtx.ai.oauth", "get_valid_token"),
            ("load_copilot_credentials", "vtx.ai.oauth", "load_credentials"),
            ("copilot_login", "vtx.ai.oauth", "login"),
            ("ProviderInfo", "vtx.ai.provider_catalog", "ProviderInfo"),
            ("detect_provider_from_env", "vtx.ai.provider_catalog", "detect_provider_from_env"),
            ("list_providers", "vtx.ai.provider_catalog", "list_providers"),
            ("get_provider_info", "vtx.ai.provider_catalog", "get"),
            ("PROVIDER_API_BY_NAME", "vtx.ai.providers", "PROVIDER_API_BY_NAME"),
            ("get_provider_class", "vtx.ai.providers", "get_provider_class"),
            ("resolve_provider_api_type", "vtx.ai.providers", "resolve_provider_api_type"),
        ],
    ),
    (
        "vtx.agent",
        [
            ("CommandOutcome", "vtx.plugins.api", "CommandOutcome"),
            ("ExtensionAPI", "vtx.plugins.api", "ExtensionAPI"),
            ("ExtensionCommand", "vtx.plugins.api", "ExtensionCommand"),
            ("ExtensionRecord", "vtx.plugins.api", "ExtensionRecord"),
            ("ExtensionTool", "vtx.plugins.api", "ExtensionTool"),
            ("AGENT_CHANGED", "vtx.plugins.event_contract", "AGENT_CHANGED"),
            ("AGENT_START", "vtx.plugins.event_contract", "AGENT_START"),
            ("LoadedExtensions", "vtx.plugins.extensions", "LoadedExtensions"),
            ("load_for_runtime", "vtx.plugins.extensions", "load_for_runtime"),
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
