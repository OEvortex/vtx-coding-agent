"""LLM provider shim — delegates to vtx.

All provider abstractions now live in vtx. This package is a
compatibility shim so that existing vtx_claw code can still
import from vtx_claw.providers.*.
"""

from __future__ import annotations

from vtx_claw.providers.base import GenerationSettings, LLMProvider, LLMResponse, ToolCallRequest
from vtx_claw.providers.factory import ProviderSnapshot, build_provider_snapshot
from vtx_claw.providers.image_generation import (
    GeneratedImageResponse,
    ImageGenerationError,
    get_image_gen_provider,
    image_gen_provider_configs,
    image_gen_provider_names,
)
from vtx_claw.providers.registry import (
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
