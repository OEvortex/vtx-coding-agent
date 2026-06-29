from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from vtx_claw.config.schema import VoiceConfig

logger = logging.getLogger(__name__)


class DeepgramSTT:
    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    async def transcribe(self, audio_bytes: bytes) -> str:
        return ""
