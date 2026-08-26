"""
Model types and catalog.

Model metadata is fetched dynamically via the provider catalog
and models.dev API. Only the type definitions live here.
"""

from dataclasses import dataclass


class ApiType:
    OPENAI_SDK = "openai-sdk"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC = "anthropic"

    _VALUES: frozenset[str] = frozenset({OPENAI_SDK, OPENAI_RESPONSES, ANTHROPIC})

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
    max_tokens: int | None
    supports_images: bool
    supports_thinking: bool
    context_window: int | None = None
    supports_tools: bool = True
    supports_audio: bool = False
    api_model_id: str = ""
    is_free: bool = False
    # Thinking-level map derived from models.dev reasoning_options:
    # level -> provider effort string, or None when explicitly unsupported.
    thinking_level_map: dict[str, str | None] | None = None

    @property
    def effective_id(self) -> str:
        return self.api_model_id or self.id


def get_model(model_id: str, provider: str | None = None) -> Model | None:
    from vtx.ai.dynamic_models import find_dynamic_model
    from vtx.ai.provider_catalog import find_model

    model = find_model(model_id, provider)
    if model is None:
        model = find_dynamic_model(model_id, provider)
    return model


def get_all_models() -> list[Model]:
    from vtx.ai.provider_catalog import get_all_catalog_models

    # ``get_all_catalog_models`` already merges the cached dynamic models via
    # ``get_fetched_models`` (model_fetcher) and the dynamic registry. The
    # previous implementation also appended ``get_dynamic_models()`` which
    # duplicated every entry; we keep a single source and dedupe for safety.
    return dedupe_models(get_all_catalog_models())


def dedupe_models(models: list[Model]) -> list[Model]:
    """Return ``models`` with the first occurrence of each (provider, id) kept."""
    seen: set[tuple[str, str]] = set()
    deduped: list[Model] = []
    for m in models:
        key = (m.provider, m.id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(m)
    return deduped


def get_models_by_provider(provider: str) -> list[Model]:
    return [m for m in get_all_models() if m.provider == provider]


def get_max_tokens(model_id: str) -> int | None:
    model = get_model(model_id)
    return model.max_tokens if model else None
