"""LLM provider shim — delegates to vtx.

All provider abstractions now live in vtx. This package is a
compatibility shim so that existing agenite_claw code can still
import from agenite_claw.providers.*.
"""

from __future__ import annotations

from agenite_claw.providers.base import (
    GenerationSettings,
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
)
from agenite_claw.providers.factory import ProviderSnapshot, build_provider_snapshot
from agenite_claw.providers.image_generation import (
    GeneratedImageResponse,
    ImageGenerationError,
    get_image_gen_provider,
    image_gen_provider_configs,
    image_gen_provider_names,
)
from agenite_claw.providers.registry import (
    ProviderSpec,
    create_dynamic_spec,
    find_by_name,
    list_providers,
)

__all__ = [
    "GeneratedImageResponse",
    "GenerationSettings",
    "ImageGenerationError",
    "LLMProvider",
    "LLMResponse",
    "ProviderSnapshot",
    "ProviderSpec",
    "ToolCallRequest",
    "create_dynamic_spec",
    "find_by_name",
    "get_image_gen_provider",
    "image_gen_provider_configs",
    "image_gen_provider_names",
    "list_providers",
]
