"""Provider catalogue, models, and credential helpers.

Re-exported lazily through :func:`__getattr__`. This package used to import every
provider client eagerly, so ``import vtx.ai`` pulled in the whole ``aiohttp`` /
``httpx`` stack (~170ms) even for a caller that only wanted
``PROVIDER_API_BY_NAME`` — which lives in :mod:`vtx.ai.providers` and needs only
the base provider layer.

``_SOURCES`` is ordered so each name still resolves to the object the eager
re-exports produced: ``ApiType`` and ``Model`` exist in both ``.models`` and
``.dynamic_models``, and the original bound them from ``.models``.
"""

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .base import DEFAULT_THINKING_LEVELS, BaseProvider, LLMStream, ProviderConfig
    from .dynamic_models import (
        DYNAMIC_PROVIDERS,
        DynamicModelEntry,
        DynamicProviderConfig,
        find_dynamic_model,
        get_all_models_with_dynamic,
        get_dynamic_models,
        get_dynamic_provider,
        get_dynamic_provider_headers,
        get_provider_models,
        refresh_all_providers,
        refresh_provider,
        register_dynamic_provider,
    )
    from .models import (
        ApiType,
        Model,
        get_all_models,
        get_max_tokens,
        get_model,
        get_models_by_provider,
    )
    from .oauth import (
        CodexCredentials,
        clear_api_key,
        clear_cline_credentials,
        clear_codex_credentials,
        cline_login,
        codex_login,
        get_codex_auth_path,
        get_dynamic_api_key,
        get_provider_status,
        get_valid_cline_credentials,
        get_valid_cline_token,
        get_valid_cline_token_sync,
        get_valid_codex_credentials,
        get_valid_codex_token,
        get_valid_codex_token_sync,
        get_valid_copilot_token_sync,
        has_api_key,
        is_cline_logged_in,
        is_codex_logged_in,
        is_copilot_logged_in,
        load_api_key,
        load_cline_credentials,
        load_codex_credentials,
        login,
        refresh_cline_token,
        refresh_codex_token,
        save_api_key,
        save_codex_credentials,
    )
    from .provider_catalog import ProviderInfo, detect_provider_from_env, list_providers
    from .providers import PROVIDER_API_BY_NAME, get_provider_class, resolve_provider_api_type

_SOURCES = (
    "vtx.ai.base",
    "vtx.ai.models",
    "vtx.ai.dynamic_models",
    "vtx.ai.oauth",
    "vtx.ai.provider_catalog",
    "vtx.ai.providers",
)

# Re-exports whose exported name differs from the attribute on its source module.
_ALIASES = {
    "clear_copilot_credentials": ("vtx.ai.oauth", "clear_credentials"),
    "copilot_login": ("vtx.ai.oauth", "login"),
    "get_copilot_token": ("vtx.ai.oauth", "get_valid_token"),
    "get_provider_info": ("vtx.ai.provider_catalog", "get"),
    "load_copilot_credentials": ("vtx.ai.oauth", "load_credentials"),
}


def __getattr__(name: str) -> Any:
    if (alias := _ALIASES.get(name)) is not None:
        module_name, attribute = alias
        value = getattr(importlib.import_module(module_name), attribute)
        globals()[name] = value
        return value
    for module_name in _SOURCES:
        module = importlib.import_module(module_name)
        if (value := getattr(module, name, None)) is not None:
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "DEFAULT_THINKING_LEVELS",
    "DYNAMIC_PROVIDERS",
    "PROVIDER_API_BY_NAME",
    "ApiType",
    "BaseProvider",
    "CodexCredentials",
    "DynamicModelEntry",
    "DynamicProviderConfig",
    "LLMStream",
    "Model",
    "ProviderConfig",
    "ProviderInfo",
    "clear_api_key",
    "clear_cline_credentials",
    "clear_codex_credentials",
    "clear_copilot_credentials",
    "cline_login",
    "codex_login",
    "copilot_login",
    "detect_provider_from_env",
    "find_dynamic_model",
    "get_all_models",
    "get_all_models_with_dynamic",
    "get_codex_auth_path",
    "get_copilot_token",
    "get_dynamic_api_key",
    "get_dynamic_models",
    "get_dynamic_provider",
    "get_dynamic_provider_headers",
    "get_max_tokens",
    "get_model",
    "get_models_by_provider",
    "get_provider_class",
    "get_provider_info",
    "get_provider_models",
    "get_provider_status",
    "get_valid_cline_credentials",
    "get_valid_cline_token",
    "get_valid_cline_token_sync",
    "get_valid_codex_credentials",
    "get_valid_codex_token",
    "get_valid_codex_token_sync",
    "get_valid_copilot_token_sync",
    "get_valid_token",
    "has_api_key",
    "is_cline_logged_in",
    "is_codex_logged_in",
    "is_copilot_logged_in",
    "list_providers",
    "load_api_key",
    "load_cline_credentials",
    "load_codex_credentials",
    "load_copilot_credentials",
    "login",
    "refresh_all_providers",
    "refresh_cline_token",
    "refresh_codex_token",
    "refresh_provider",
    "register_dynamic_provider",
    "resolve_provider_api_type",
    "save_api_key",
    "save_codex_credentials",
]
