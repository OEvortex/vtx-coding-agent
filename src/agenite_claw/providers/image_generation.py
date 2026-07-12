"""Image generation provider helpers."""

from __future__ import annotations

import base64
import binascii
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from agenite_claw.providers.registry import find_by_name
from agenite_claw.utils.helpers import detect_image_mime

_OPENROUTER_ATTRIBUTION_HEADERS = {
    "HTTP-Referer": "https://github.com/HKUDS/agenite_claw",
    "X-OpenRouter-Title": "agenite_claw",
    "X-OpenRouter-Categories": "cli-agent,personal-agent",
}
_DEFAULT_TIMEOUT_S = 120.0
_AIHUBMIX_TIMEOUT_S = 300.0
_AIHUBMIX_ASPECT_RATIO_SIZES = {
    "1:1": "1024x1024",
    "3:4": "1024x1536",
    "9:16": "1024x1536",
    "4:3": "1536x1024",
    "16:9": "1536x1024",
}
_GEMINI_DEFAULT_TIMEOUT_S = 120.0
_GEMINI_IMAGEN_ASPECT_RATIOS = {"1:1", "9:16", "16:9", "3:4", "4:3"}
_OLLAMA_DEFAULT_SIDE = 1024
_OLLAMA_SIZE_PRESETS = {"1K": 1024, "2K": 2048, "4K": 4096}
_OLLAMA_EXPLICIT_SIZE_RE = re.compile(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$")
_OLLAMA_ASPECT_RATIO_RE = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*$")


class ImageGenerationError(RuntimeError):
    """Raised when the image generation provider cannot return images."""


@dataclass(frozen=True)
class GeneratedImageResponse:
    """Images and optional text returned by the provider."""

    images: list[str]
    content: str
    raw: dict[str, Any]


def _read_image_b64(path: str | Path) -> tuple[str, str]:
    """Return ``(mime, base64)`` for the image at ``path``."""
    p = Path(path).expanduser()
    raw = p.read_bytes()
    mime = detect_image_mime(raw)
    if mime is None:
        raise ImageGenerationError(f"unsupported reference image: {p}")
    return mime, base64.b64encode(raw).decode("ascii")


def image_path_to_data_url(path: str | Path) -> str:
    """Convert a local image path to an image data URL."""
    mime, encoded = _read_image_b64(path)
    return f"data:{mime};base64,{encoded}"


def image_path_to_inline_data(path: str | Path) -> dict[str, str]:
    """Convert a local image path to a Gemini ``inlineData`` payload dict."""
    mime, encoded = _read_image_b64(path)
    return {"mimeType": mime, "data": encoded}


def _b64_image_data_url(value: str) -> str:
    encoded = "".join(value.split())
    try:
        raw = base64.b64decode(encoded, validate=True)
    except binascii.Error as exc:
        raise ImageGenerationError("generated image payload was not valid base64") from exc
    mime = detect_image_mime(raw)
    if mime is None:
        raise ImageGenerationError("generated image payload was not a supported image")
    return f"data:{mime};base64,{encoded}"


def _aihubmix_size(aspect_ratio: str | None, image_size: str | None) -> str:
    """Return an OpenAI Images API size string for AIHubMix.

    The WebUI emits compact size hints like ``1K`` for OpenRouter. AIHubMix's
    Images API expects OpenAI-style dimensions or ``auto``, so only pass
    through explicit dimension strings and otherwise derive the closest
    supported orientation from aspect ratio.
    """
    if image_size and "x" in image_size.lower():
        return image_size
    if aspect_ratio in _AIHUBMIX_ASPECT_RATIO_SIZES:
        return _AIHUBMIX_ASPECT_RATIO_SIZES[aspect_ratio]
    return "auto"


def _aihubmix_model_path(model: str) -> str:
    if "/" in model:
        return model
    if model.startswith(("gpt-image-", "dall-e-")):
        return f"openai/{model}"
    return model


async def _download_image_data_url(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = response.text[:500]
        raise ImageGenerationError(f"failed to download generated image: {detail}") from exc
    raw = response.content
    mime = detect_image_mime(raw)
    if mime is None:
        raise ImageGenerationError("generated image URL did not return a supported image")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_IMAGE_GEN_PROVIDERS: dict[str, type[ImageGenerationProvider]] = {}


def register_image_gen_provider(cls: type[ImageGenerationProvider]) -> None:
    """Register an image provider at import time only.

    The registry is populated by module side effects so provider discovery
    stays lazy and consistent across the process.
    """
    name = cls.provider_name
    if not name:
        raise ValueError(f"{cls.__name__} must set provider_name")
    _IMAGE_GEN_PROVIDERS[name] = cls


def get_image_gen_provider(name: str) -> type[ImageGenerationProvider] | None:
    return _IMAGE_GEN_PROVIDERS.get(name)


def image_gen_provider_names() -> tuple[str, ...]:
    """Return registered image generation provider names in registry order."""
    return tuple(_IMAGE_GEN_PROVIDERS)


def image_gen_provider_configs(config: Any) -> dict[str, Any]:
    providers_cfg = config.providers
    return {
        name: pc
        for name in _IMAGE_GEN_PROVIDERS
        if (pc := getattr(providers_cfg, name, None)) is not None
    }


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class ImageGenerationProvider(ABC):
    """Base class for image generation provider clients."""

    provider_name: str = ""
    missing_key_message: str = ""
    default_timeout: float = _DEFAULT_TIMEOUT_S

    def __init__(
        self,
        *,
        api_key: str | None,
        api_base: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_base = self._resolve_base_url(api_base)
        self.extra_headers = extra_headers or {}
        self.extra_body = extra_body or {}
        self.timeout = timeout if timeout is not None else self.default_timeout
        self._client = client

    def _resolve_base_url(self, api_base: str | None) -> str:
        if api_base:
            return api_base.rstrip("/")
        spec = find_by_name(self.provider_name)
        if spec and spec.default_api_base:
            return spec.default_api_base.rstrip("/")
        return self._default_base_url()

    def _default_base_url(self) -> str:
        return ""

    @abstractmethod
    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        reference_images: list[str] | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
    ) -> GeneratedImageResponse: ...

    def _require_images(self, images: list[str], data: dict[str, Any]) -> None:
        if images:
            return
        provider_error = data.get("error") if isinstance(data, dict) else None
        label = self.provider_name
        if provider_error:
            raise ImageGenerationError(f"{label} returned no images: {provider_error}")
        raise ImageGenerationError(f"{label} returned no images for this request")

    async def _http_post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any],
        client: httpx.AsyncClient | None = None,
    ) -> httpx.Response:
        if client is not None:
            return await client.post(url, headers=headers, json=body)
        if self._client is not None:
            return await self._client.post(url, headers=headers, json=body)
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            return await c.post(url, headers=headers, json=body)


class OpenRouterImageGenerationClient(ImageGenerationProvider):
    """Small async client for OpenRouter Chat Completions image generation."""

    provider_name = "openrouter"
    missing_key_message = "OpenRouter API key is not configured. Set providers.openrouter.apiKey."

    def _default_base_url(self) -> str:
        return "https://openrouter.ai/api/v1"

    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        reference_images: list[str] | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
    ) -> GeneratedImageResponse:
        if not self.api_key:
            raise ImageGenerationError(self.missing_key_message)

        content: str | list[dict[str, Any]]
        references = list(reference_images or [])
        if references:
            blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            blocks.extend(
                {"type": "image_url", "image_url": {"url": image_path_to_data_url(path)}}
                for path in references
            )
            content = blocks
        else:
            content = prompt

        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "modalities": ["image", "text"],
            "stream": False,
        }
        image_config: dict[str, str] = {}
        if aspect_ratio:
            image_config["aspect_ratio"] = aspect_ratio
        if image_size:
            image_config["image_size"] = image_size
        if image_config:
            body["image_config"] = image_config
        body.update(self.extra_body)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **_OPENROUTER_ATTRIBUTION_HEADERS,
            **self.extra_headers,
        }
        url = f"{self.api_base}/chat/completions"
        response = await self._http_post(url, headers=headers, body=body)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:500]
            raise ImageGenerationError(f"OpenRouter image generation failed: {detail}") from exc

        payload = response.json()
        choices = payload.get("choices", [])
        if not choices:
            raise ImageGenerationError("OpenRouter returned no choices")

        images: list[str] = []
        content_text = ""
        for choice in choices:
            message = choice.get("message", {})
            content_parts = message.get("content", "")
            if isinstance(content_parts, list):
                for part in content_parts:
                    if part.get("type") == "image_url":
                        images.append(part["image_url"]["url"])
                    elif part.get("type") == "text":
                        content_text += part["text"]
            else:
                content_text = str(content_parts)

        # Fallback: extract from content string for standard text responses
        if not images and not content_text:
            content_text = str(payload)

        self._require_images(images, payload)
        return GeneratedImageResponse(images=images, content=content_text, raw=payload)
