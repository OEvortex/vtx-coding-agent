"""Incremental SSE decoder shared by the raw-HTTP OpenAI SDKs."""

from __future__ import annotations

import codecs
import json
from collections.abc import AsyncIterator
from typing import Any

import aiohttp


async def iter_sse_json(response: aiohttp.ClientResponse) -> AsyncIterator[dict[str, Any]]:
    """Yield the JSON payload of each ``data:`` line in an SSE response.

    The wire format is line-based UTF-8; decoding is incremental so a chunk
    boundary may split a multi-byte character or a line. Blank lines and the
    ``[DONE]`` sentinel are skipped, as are malformed payloads.
    """
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    buffer = ""
    async for raw in response.content.iter_any():
        buffer += decoder.decode(raw)
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.rstrip("\r")
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                value = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value
