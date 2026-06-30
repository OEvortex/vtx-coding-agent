"""LLM provider shim — delegates to vtx.

All provider abstractions now live in vtx. This package is a
compatibility shim so that existing vtx_claw code can still
import from vtx_claw.providers.*.
"""

from __future__ import annotations

from vtx_claw.providers.base import LLMProvider, LLMResponse, ToolCallRequest, GenerationSettings
from vtx_claw.providers.factory import ProviderSnapshot, build_provider_snapshot
from vtx_claw.providers.registry import (
    ProviderSpec,
    create_dynamic_spec,
    find_by_name,
    list_providers,
)
from vtx_claw.providers.image_generation import (
    ImageGenerationError,
    GeneratedImageResponse,
    get_image_gen_provider,
    image_gen_provider_configs,
    image_gen_provider_names,
)

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "ToolCallRequest",
    "GenerationSettings",
    "ProviderSnapshot",
    "ProviderSpec",
    "find_by_name",
    "list_providers",
    "create_dynamic_spec",
    "ImageGenerationError",
    "GeneratedImageResponse",
    "get_image_gen_provider",
    "image_gen_provider_configs",
    "image_gen_provider_names",
]
