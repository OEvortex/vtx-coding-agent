"""Provider-specific voice transcription adapters.

This module only knows how to call external transcription APIs such as Groq,
OpenAI Whisper, OpenRouter, Xiaomi MiMo ASR, and AssemblyAI. Product-level config fallback,
WebUI upload validation, and channel integration live in
``agenite_claw.audio.transcription``.
"""

import asyncio
import mimetypes
from pathlib import Path
from typing import Any, cast

import httpx
from loguru import logger

_CHAT_COMPLETIONS_PATH = "chat/completions"
_TRANSCRIPTIONS_PATH = "audio/transcriptions"
_STEPFUN_ASR_PATH = "audio/asr/sse"
_ASSEMBLYAI_DEFAULT_API_BASE = "https://api.assemblyai.com/v2"
_ASSEMBLYAI_POLL_ATTEMPTS = 60
_ASSEMBLYAI_POLL_INTERVAL_S = 2.0
_AUDIO_MIME_OVERRIDES = {
    ".m4a": "audio/mp4",
    ".mpga": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".weba": "audio/webm",
    ".webm": "audio/webm",
}
_FORMAT_ALIASES = {"oga": "ogg", "opus": "ogg", "mpga": "mp3", "mpeg": "mp3", "mp4": "m4a"}


def _resolve_transcription_url(api_base: str | None, default_url: str) -> str:
    """Resolve the full transcription endpoint URL.

    Accepts either a chat-style base (e.g. ``https://api.groq.com/openai/v1``)
    or a complete URL already ending in ``/audio/transcriptions``. A chat-style
    base — the form users naturally copy from their LLM provider config — gets
    the path appended instead of being POSTed verbatim and 404ing (#3637).
    """
    if not api_base:
        return default_url
    base = api_base.rstrip("/")
    if base.endswith(_TRANSCRIPTIONS_PATH):
        return base
    return f"{base}/{_TRANSCRIPTIONS_PATH}"


def _resolve_chat_completions_url(api_base: str | None, default_url: str) -> str:
    """Resolve a chat-completions endpoint for ASR providers using chat payloads."""
    if not api_base:
        return default_url
    base = api_base.rstrip("/")
    if base.endswith(_CHAT_COMPLETIONS_PATH):
        return base
    return f"{base}/{_CHAT_COMPLETIONS_PATH}"


def _resolve_api_path(api_base: str | None, default_base: str, path: str) -> str:
    base = (api_base or default_base).rstrip("/")
    return f"{base}/{path.lstrip('/')}"


def _resolve_stepfun_asr_url(api_base: str | None) -> str:
    base = (api_base or "https://api.stepfun.com/v1").rstrip("/")
    if base.endswith(_STEPFUN_ASR_PATH):
        return base
    return f"{base}/{_STEPFUN_ASR_PATH}"


def _audio_mime_type(path: Path) -> str:
    return (
        _AUDIO_MIME_OVERRIDES.get(path.suffix.lower())
        or mimetypes.guess_type(path.name)[0]
        or "application/octet-stream"
    )


def _audio_format(path: Path) -> str:
    """Map an audio file's extension to an OpenRouter ``format`` value."""
    ext = path.suffix.lstrip(".").lower()
    return _FORMAT_ALIASES.get(ext, ext)


# Up to 3 retries (4 attempts total) with exponential backoff on transient
# failures. Whisper endpoints occasionally return 502/503 under load, and
# mobile-network transcription callers hit sporadic connect/read errors.
# Without this, a voice message silently becomes the empty string.
_MAX_RETRIES = 3
_BACKOFF_S = (1.0, 2.0, 4.0)
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
_RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
)


async def _request_json_with_retry(
    client: httpx.AsyncClient, method: str, url: str, *, provider_label: str, **kwargs: object
) -> dict[str, Any] | None:
    for attempt in range(_MAX_RETRIES + 1):
        try:
            request = getattr(client, method.lower(), None)
            if request is None:
                response = await client.request(method, url, **cast(Any, kwargs))
            else:
                response = await request(url, **cast(Any, kwargs))
        except _RETRYABLE_EXCEPTIONS as e:
            if attempt < _MAX_RETRIES:
                logger.warning(
                    "{} transcription transient error (attempt {}/{}) {}",
                    provider_label,
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    e,
                )
                await asyncio.sleep(_BACKOFF_S[attempt])
                continue
            logger.exception(
                "{} transcription error after {} attempts {}", provider_label, _MAX_RETRIES + 1, e
            )
            return None
        except Exception as e:
            logger.exception("{} transcription error: {}", provider_label, e)
            return None

        if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
            logger.warning(
                "{} transcription transient HTTP {} (attempt {}/{})",
                provider_label,
                response.status_code,
                attempt + 1,
                _MAX_RETRIES + 1,
            )
            await asyncio.sleep(_BACKOFF_S[attempt])
            continue

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            body = response.text.strip().replace("\n", " ")[:500]
            logger.error(
                "{} transcription HTTP {}{}{}",
                provider_label,
                response.status_code,
                f" {response.reason_phrase}" if response.reason_phrase else "",
                f": {body}" if body else "",
            )
            return None
        except Exception as e:
            logger.exception("{} transcription error: {}", provider_label, e)
            return None

        try:
            payload = response.json()
        except Exception as e:
            logger.exception(
                "{} transcription error: malformed response body: {}", provider_label, e
            )
            return None
        if not isinstance(payload, dict):
            logger.error(
                "{} transcription error: unexpected response shape: {!r}",
                provider_label,
                type(payload).__name__,
            )
            return None
        return payload
    return None


async def _post_transcription_with_retry(build_request, provider_label, parse_fn) -> None: ...


async def _post_json_transcription_with_retry(build_request, provider_label, parse_fn) -> None: ...


async def _post_xiaomi_mimo_asr_with_retry(build_request, provider_label, parse_fn) -> None: ...


async def _post_stepfun_asr_with_retry(build_request, provider_label, parse_fn) -> None: ...


class OpenAITranscriptionProvider: ...


class GroqTranscriptionProvider: ...


class OpenRouterTranscriptionProvider: ...


class XiaomiMiMoTranscriptionProvider: ...


class StepFunTranscriptionProvider: ...


def find_by_name(name: str) -> Any:
    """Find a transcription provider by name from the registry."""
    from agenite_claw.providers.registry import find_by_name as _find_by_name

    return _find_by_name(name)
