"""
Model types and catalog.

Model metadata is fetched dynamically via the provider catalog
and models.dev API. Only the type definitions live here.
"""

from dataclasses import dataclass


class ApiType:
    OPENAI_COMPLETIONS = "openai-completions"
    OPENAI_SDK = "openai-sdk"
    ANTHROPIC = "anthropic"
    SUPERCODE = "supercode"

    _VALUES: frozenset[str] = frozenset({OPENAI_COMPLETIONS, OPENAI_SDK, ANTHROPIC, SUPERCODE})

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

    @property
    def effective_id(self) -> str:
        return self.api_model_id or self.id


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
    # The catalog already includes cached dynamic models via
    # ``get_fetched_models``, and ``get_dynamic_models`` returns the same cache,
    # so every dynamic entry appears twice without dedup.
    return dedupe_models(merged)


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
