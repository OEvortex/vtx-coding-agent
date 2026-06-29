from ..base import BaseProvider
from ..models import ApiType

PROVIDER_API_BY_NAME: dict[str, ApiType] = {
    "openai": ApiType(ApiType.OPENAI_SDK),
    "anthropic": ApiType(ApiType.ANTHROPIC),
    "zhipu": ApiType(ApiType.OPENAI_SDK),
    "deepseek": ApiType(ApiType.OPENAI_SDK),
    "airouter": ApiType(ApiType.OPENAI_SDK),
    "opencode": ApiType(ApiType.OPENAI_SDK),
    "kilo": ApiType(ApiType.OPENAI_SDK),
    "tokenrouter": ApiType(ApiType.OPENAI_SDK),
    "openrouter": ApiType(ApiType.OPENAI_SDK),
    "ollama": ApiType(ApiType.OPENAI_SDK),
    "aerolink": ApiType(ApiType.ANTHROPIC),
    "zyloo": ApiType(ApiType.OPENAI_SDK),
    "opengateway": ApiType(ApiType.OPENAI_SDK),
}


def resolve_provider_api_type(provider: str | None) -> ApiType:
    if provider is None:
        return ApiType(ApiType.OPENAI_SDK)
    api_type = PROVIDER_API_BY_NAME.get(provider)
    if api_type is None:
        return ApiType(ApiType.OPENAI_SDK)
    return api_type


def get_provider_class(api_type: ApiType) -> type[BaseProvider]:
    match api_type.value:
        case ApiType.OPENAI_SDK:
            from .openai_sdk import OpenAISDKProvider

            return OpenAISDKProvider
        case ApiType.ANTHROPIC:
            from .anthropic_sdk import AnthropicSDKProvider

            return AnthropicSDKProvider
        case ApiType.OPENAI_COMPLETIONS:
            from .openai_sdk import OpenAISDKProvider

            return OpenAISDKProvider
    raise ValueError(f"Unsupported API type: {api_type.value}")


__all__ = ["PROVIDER_API_BY_NAME", "get_provider_class", "resolve_provider_api_type"]
