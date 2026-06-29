from __future__ import annotations

import asyncio
import pytest

from vtx_claw.infra.retry import RetryPolicy
from vtx_claw.infra.dedup import Deduper
from vtx_claw.infra.delivery_queue import DeliveryQueue
from vtx_claw.infra.heartbeat import HeartbeatRunner


@pytest.mark.asyncio
async def test_retry_succeeds_after_failures():
    calls = []
    policy = RetryPolicy(tries=3, delay=0.01)

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("transient")
        return "ok"

    r = await policy.call(flaky)
    assert r == "ok"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_retry_exhausts():
    policy = RetryPolicy(tries=2, delay=0.01)

    async def always_fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await policy.call(always_fail)


def test_dedup_filters_duplicates():
    d = Deduper(ttl=10)
    assert d.accept("evt-1")
    assert not d.accept("evt-1")
    assert d.accept("evt-2")
    assert len(d._seen) == 2


def test_dedup_allows_after_ttl(monkeypatch: pytest.MonkeyPatch):
    import time as _t

    times = [0.0, 0.0, 1.0]

    def fake_time() -> float:
        return times.pop(0)

    monkeypatch.setattr("vtx_claw.infra.dedup.time.time", fake_time)
    d = Deduper(ttl=0.5)
    assert d.accept("k")
    assert not d.accept("k")
    assert d.accept("k")


def test_delivery_queue():
    q = DeliveryQueue()
    assert len(q) == 0
    q.put({"a": 1})
    assert len(q) == 1
    item = q.get()
    assert item == {"a": 1}
    assert len(q) == 0


@pytest.mark.asyncio
async def test_heartbeat_stops():
    hb = HeartbeatRunner(interval=0.05)
    await hb.start(lambda: None)
    await asyncio.sleep(0.1)
    await hb.stop()
