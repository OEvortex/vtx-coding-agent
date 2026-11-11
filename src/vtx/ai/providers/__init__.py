from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import ApiType

if TYPE_CHECKING:
    from ..base import BaseProvider


def resolve_provider_api_type(provider: str | None) -> ApiType:
    """Resolve a provider slug to its transport ApiType.

    The provider catalog (``provider.yaml`` + builtin OAuth providers +
    custom providers) is the single source of truth; ``ProviderInfo.api``
    or ``family`` decides the transport. Unknown slugs that happen to name
    an ApiType value (e.g. ``"openai-responses"``) resolve directly;
    anything else falls back to ``openai-sdk``.
    """
    if provider is None:
        return ApiType(ApiType.OPENAI_SDK)
    try:
        from vtx.ai.provider_catalog import get, provider_api_type

        p = get(provider)
        if p is not None:
            return provider_api_type(p)
    except Exception:
        pass
    try:
        return ApiType(provider)
    except Exception:
        return ApiType(ApiType.OPENAI_SDK)


def get_provider_class(api_type: ApiType, provider_slug: str = "") -> type[BaseProvider]:
    match api_type.value:
        case ApiType.OPENAI_SDK:
            from vtx.ai.providers.openai_sdk import OpenAISDKProvider

            return OpenAISDKProvider
        case ApiType.ANTHROPIC:
            from vtx.ai.providers.anthropic_sdk import AnthropicSDKProvider

            return AnthropicSDKProvider
        case ApiType.OPENAI_RESPONSES:
            from vtx.ai.providers.openai_responses_sdk import OpenAIResponsesSDKProvider

            return OpenAIResponsesSDKProvider
        case ApiType.DEVIN:
            from vtx.ai.oauth.devin import DevinSDKProvider

            return DevinSDKProvider
    raise ValueError(f"Unsupported API type: {api_type.value}")


__all__ = ["get_provider_class", "resolve_provider_api_type"]
