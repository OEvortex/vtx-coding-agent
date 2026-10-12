"""Tests for the models.dev limits cache the thinking level map is read from."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path

import pytest

from vtx.ai.context_length import CACHE_TTL_SECONDS, ContextLengthManager

CATALOG = {
    "xai": {
        "models": {
            "grok-4.5": {
                "reasoning": True,
                "reasoning_options": [{"type": "effort", "values": ["low", "medium", "high"]}],
                "limit": {"context": 500000, "output": 50000},
                "modalities": {"input": ["text"], "output": ["text"]},
            }
        }
    }
}

FRESH_CATALOG = {
    "xai": {
        "models": {
            "grok-5.1": {
                "reasoning": True,
                "reasoning_options": [{"type": "effort", "values": ["low", "high"]}],
                "limit": {"context": 900000, "output": 90000},
                "modalities": {"input": ["text"], "output": ["text"]},
            }
        }
    }
}


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A manager whose cache file lives under ``tmp_path`` (never the real one)."""
    monkeypatch.setattr("vtx.core.paths.get_config_dir", lambda: tmp_path)
    return ContextLengthManager(), tmp_path / "models_dev_limits.json"


def _write_cache(path: Path, payload: dict, *, age_seconds: float) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    when = time.time() - age_seconds
    os.utime(path, (when, when))


def _blocking_fetch(monkeypatch: pytest.MonkeyPatch, payload: dict) -> threading.Event:
    """Serve ``payload`` only once ``event`` is set, so the fetch cannot race the test."""
    release = threading.Event()

    class _Response:
        def read(self) -> bytes:
            return json.dumps(payload).encode("utf-8")

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

    def _fake_urlopen(*_args: object, **_kwargs: object) -> _Response:
        release.wait(timeout=10)
        return _Response()

    monkeypatch.setattr("vtx.ai.context_length.urlopen", _fake_urlopen)
    return release


async def _wait_for_refresh(manager: ContextLengthManager) -> None:
    while manager._refreshing:
        await asyncio.sleep(0.02)


def test_expired_cache_is_still_loaded(cache):
    """An expired cache must be served, not dropped.

    The TUI resolves limits from the event loop and a fetch there would
    freeze the UI, so a stale cache used to leave ``_limits`` empty: no
    context window and no reasoning-effort map for any model, which makes
    ``/thinking`` offer only "default" - a level that is never sent.
    """
    manager, path = cache
    _write_cache(path, CATALOG, age_seconds=CACHE_TTL_SECONDS + 60)
    fetched: list[str] = []
    manager._fetch_and_parse = lambda: fetched.append("yes")  # type: ignore[method-assign]

    limits = manager.get_limits("grok-4.5")

    assert limits.context == 500000
    assert limits.thinking_level_map == {
        "off": None,
        "minimal": None,
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": None,
        "max": None,
    }
    assert manager._stale is True
    # ...and the expiry is noticed, so a refresh happens instead of nothing.
    assert fetched == ["yes"]


def test_fresh_cache_is_not_refreshed(cache):
    manager, path = cache
    _write_cache(path, CATALOG, age_seconds=60)

    def _boom() -> None:
        raise AssertionError("a fresh cache must not trigger a fetch")

    manager._fetch_and_parse = _boom  # type: ignore[method-assign]

    assert manager.get_limits("grok-4.5").context == 500000
    assert manager._stale is False


@pytest.mark.asyncio
async def test_expired_cache_refreshes_in_background_inside_loop(cache, monkeypatch):
    """Inside a running loop the refresh must not block, but must still happen.

    Callers used to get nothing at all: ``ensure_loaded`` saw the loop, gave
    up on the network, and the process ran on empty limits for its lifetime.
    """
    manager, path = cache
    _write_cache(path, CATALOG, age_seconds=CACHE_TTL_SECONDS + 60)
    release = _blocking_fetch(monkeypatch, FRESH_CATALOG)

    # The stale cache answers immediately; the refresh is a background thread.
    assert manager.get_limits("grok-4.5").context == 500000
    assert manager._refreshing is True
    assert manager._stale is True

    release.set()
    await asyncio.wait_for(_wait_for_refresh(manager), timeout=10)

    assert manager._stale is False
    assert manager.get_limits("grok-5.1").context == 900000
    assert manager.get_limits("grok-5.1").thinking_level_map == {
        "off": None,
        "minimal": None,
        "low": "low",
        "medium": None,
        "high": "high",
        "xhigh": None,
        "max": None,
    }
    assert json.loads(path.read_text(encoding="utf-8")) == FRESH_CATALOG


@pytest.mark.asyncio
async def test_missing_cache_fetches_without_blocking_the_loop(cache, monkeypatch):
    """A fresh install must not freeze the event loop on a 10s urlopen."""
    manager, _path = cache
    release = _blocking_fetch(monkeypatch, FRESH_CATALOG)

    started = time.monotonic()
    assert manager.get_limits("grok-5.1").context == 0
    assert manager._refreshing is True
    assert time.monotonic() - started < 1.0, "lookup blocked on the network"

    release.set()
    await asyncio.wait_for(_wait_for_refresh(manager), timeout=10)

    assert manager.get_limits("grok-5.1").context == 900000
    assert manager._stale is False
