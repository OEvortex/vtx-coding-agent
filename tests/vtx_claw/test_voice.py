from __future__ import annotations

import pytest

from vtx_claw.voice import DeepgramSTT


@pytest.mark.asyncio
async def test_voice_disabled_returns_empty():
    stt = DeepgramSTT("")
    assert await stt.transcribe(b"") == ""


@pytest.mark.asyncio
async def test_voice_with_key_returns_empty():
    stt = DeepgramSTT("dg-key")
    assert await stt.transcribe(b"audio") == ""
