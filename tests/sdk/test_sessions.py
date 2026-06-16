"""Tests for session backends."""

from __future__ import annotations

from pathlib import Path

import pytest

from vtx.sdk.sessions import InMemorySession, JSONLSession, Session


@pytest.mark.asyncio
async def test_in_memory_session_basic() -> None:
    s = InMemorySession("abc")
    assert s.session_id == "abc"
    assert await s.get_items() == []
    await s.add_items([{"role": "user", "content": "hi"}])
    items = await s.get_items()
    assert len(items) == 1
    assert items[0]["content"] == "hi"


@pytest.mark.asyncio
async def test_in_memory_session_limit() -> None:
    s = InMemorySession()
    for i in range(5):
        await s.add_items([{"role": "user", "content": str(i)}])
    items = await s.get_items(limit=2)
    assert len(items) == 2
    assert items[0]["content"] == "3"
    assert items[1]["content"] == "4"


@pytest.mark.asyncio
async def test_in_memory_session_pop() -> None:
    s = InMemorySession()
    await s.add_items([{"role": "user", "content": "a"}])
    await s.add_items([{"role": "assistant", "content": "b"}])
    last = await s.pop_item()
    assert last is not None
    assert last["content"] == "b"
    items = await s.get_items()
    assert len(items) == 1


@pytest.mark.asyncio
async def test_in_memory_session_pop_empty() -> None:
    s = InMemorySession()
    assert await s.pop_item() is None


@pytest.mark.asyncio
async def test_in_memory_session_clear() -> None:
    s = InMemorySession()
    await s.add_items([{"role": "user", "content": "x"}])
    await s.clear_session()
    assert await s.get_items() == []


def test_in_memory_session_is_protocol_compatible() -> None:
    """``InMemorySession`` satisfies the ``Session`` protocol."""
    s = InMemorySession()
    assert isinstance(s, Session)


@pytest.mark.asyncio
async def test_jsonl_session_persistence(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    s = JSONLSession(path)
    await s.add_items([{"role": "user", "content": "hello"}])
    await s.add_items([{"role": "assistant", "content": "world"}])

    # Reload from disk.
    s2 = JSONLSession(path)
    items = await s2.get_items()
    assert len(items) >= 2
    # The user message's content is the string "hello".
    user_contents = [
        i.get("content")
        for i in items
        if i.get("role") == "user" and isinstance(i.get("content"), str)
    ]
    assert "hello" in user_contents
    # The assistant message's content is a list of content parts.
    assistant = [i for i in items if i.get("role") == "assistant"]
    assert len(assistant) >= 1
    text = ""
    for part in assistant[0].get("content", []):
        if isinstance(part, dict) and part.get("type") == "text":
            text += part.get("text", "")
    assert text == "world"


@pytest.mark.asyncio
async def test_jsonl_session_no_path() -> None:
    s = JSONLSession()  # no path, in-memory only
    await s.add_items([{"role": "user", "content": "x"}])
    items = await s.get_items()
    assert len(items) == 1
