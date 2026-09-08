"""Image generation providers for openjarvis."""

from __future__ import annotations

from typing import Any, ClassVar


class ImageGenerationError(Exception):
    """Raised when an image generation request fails."""


class ImageGenerationResponse:
    """Result of an image generation request (data URLs or raw payloads)."""

    def __init__(self, images: list[str]) -> None:
        self.images = images


class ImageGenerationProvider:
    """Base class for image generation provider clients."""

    name: ClassVar[str] = ""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base
        self.extra_headers = dict(extra_headers or {})
        self.extra_body = dict(extra_body or {})

    async def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
        reference_images: list[str] | None = None,
        **kwargs: Any,
    ) -> ImageGenerationResponse:
        """Generate images; the response exposes an ``images`` list of data URLs."""
        raise NotImplementedError(f"Image generation provider '{self.name}' is not implemented")


_REGISTRY: dict[str, type[ImageGenerationProvider]] = {}


def register_image_gen_provider(cls: type[ImageGenerationProvider]) -> None:
    if cls.name:
        _REGISTRY[cls.name] = cls


def get_image_gen_provider(name: str) -> type[ImageGenerationProvider] | None:
    """Return the provider class registered under ``name`` (None if unknown)."""
    return _REGISTRY.get(name)


__all__ = [
    "ImageGenerationError",
    "ImageGenerationProvider",
    "ImageGenerationResponse",
    "get_image_gen_provider",
    "register_image_gen_provider",
]
