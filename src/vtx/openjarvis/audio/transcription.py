"""Audio transcription for openjarvis channels (Whisper via OpenAI/Groq)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger


def resolve_transcription_config(config: Any) -> dict[str, Any]:
    """Extract the transcription settings section from the loaded config."""
    section = getattr(config, "transcription", None)
    if section is None:
        section = config.get("transcription") if isinstance(config, dict) else None
    if section is None:
        return {}
    if hasattr(section, "model_dump"):
        return section.model_dump()
    return dict(section)


async def transcribe_audio_file(
    file_path: str | Path, config: dict[str, Any] | None = None
) -> str:
    """Transcribe an audio file, returning its text (empty string on failure).

    Requires a configured provider (``config["provider"]``) with an API key;
    without one, transcription is skipped and an empty transcript is returned
    so callers degrade gracefully.
    """
    cfg = config or {}
    provider = cfg.get("provider")
    api_key = cfg.get("api_key")
    if not provider or not api_key:
        logger.debug("Audio transcription not configured; skipping {}", file_path)
        return ""

    try:
        from openai import AsyncOpenAI

        base_url = cfg.get("api_base") or None
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        model = cfg.get("model") or "whisper-1"
        with open(file_path, "rb") as audio:
            resp = await client.audio.transcriptions.create(model=model, file=audio)
        return getattr(resp, "text", "") or ""
    except Exception:
        logger.exception("Audio transcription failed for {}", file_path)
        return ""


__all__ = ["resolve_transcription_config", "transcribe_audio_file"]
