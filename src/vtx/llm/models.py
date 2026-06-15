"""
Model types and catalog.

Model metadata is fetched dynamically via the provider catalog
and models.dev API. Only the type definitions live here.
"""

from dataclasses import dataclass

DEFAULT_MAX_TOKENS = 16384


class ApiType:
    OPENAI_COMPLETIONS = "openai-completions"
    OPENAI_SDK = "openai-sdk"
    ANTHROPIC = "anthropic"

    _VALUES: frozenset[str] = frozenset({OPENAI_COMPLETIONS, OPENAI_SDK, ANTHROPIC})

    def __init__(self, value: str):
        if value not in self._VALUES:
            raise ValueError(f"Invalid ApiType: {value}")
        self.value = value

    def __eq__(self, other):
        if isinstance(other, ApiType):
            return self.value == other.value
        if isinstance(other, str):
            return self.value == other
        return NotImplemented

    def __hash__(self):
        return hash(self.value)

    def __repr__(self):
        return f"ApiType({self.value!r})"


@dataclass
class Model:
    id: str
    provider: str
    api: ApiType
    base_url: str
    max_tokens: int
    supports_images: bool
    supports_thinking: bool
    context_window: int | None = None
    supports_tools: bool = True
    supports_audio: bool = False


def get_model(model_id: str, provider: str | None = None) -> Model | None:
    from .dynamic_models import find_dynamic_model
    from .provider_catalog import find_model

    model = find_model(model_id, provider)
    if model is None:
        model = find_dynamic_model(model_id, provider)
    return model


def get_all_models() -> list[Model]:
    from .dynamic_models import get_dynamic_models
    from .provider_catalog import get_all_catalog_models

    merged: list[Model] = get_all_catalog_models()
    merged.extend(get_dynamic_models())
    return merged


def get_models_by_provider(provider: str) -> list[Model]:
    return [m for m in get_all_models() if m.provider == provider]


def get_max_tokens(model_id: str) -> int:
    model = get_model(model_id)
    return model.max_tokens if model else DEFAULT_MAX_TOKENS
