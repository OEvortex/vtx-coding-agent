"""Tests for the sub-agent admission queue (vtx.agent.subagents)."""

import asyncio

import pytest

from vtx.agent.subagents import SubagentScheduler


class TestAdmission:
    def test_runs_up_to_the_limit_without_queueing(self) -> None:
        async def scenario() -> None:
            scheduler = SubagentScheduler(limit=2)
            await scheduler.acquire()
            await scheduler.acquire()
            assert (scheduler.running, scheduler.queued) == (2, 0)

        asyncio.run(scenario())

    def test_limit_zero_is_unlimited(self) -> None:
        async def scenario() -> None:
            scheduler = SubagentScheduler(limit=0)
            for _ in range(25):
                await scheduler.acquire()
            assert (scheduler.running, scheduler.queued) == (25, 0)

        asyncio.run(scenario())

    def test_excess_subagents_wait_for_a_slot(self) -> None:
        async def scenario() -> list[int]:
            scheduler = SubagentScheduler(limit=2)
            started: list[int] = []
            done = asyncio.Event()

            async def worker(index: int) -> None:
                await scheduler.acquire()
                started.append(index)
                if len(started) == 4:
                    done.set()
                await asyncio.sleep(0.05)
                scheduler.release()

            workers = [asyncio.create_task(worker(i)) for i in range(4)]
            # Give the first two a chance to take the slots, then confirm the
            # rest are parked rather than piling in.
            await asyncio.sleep(0.01)
            assert (scheduler.running, scheduler.queued) == (2, 2)
            await asyncio.wait_for(done.wait(), timeout=2)
            await asyncio.gather(*workers)
            return started

        started = asyncio.run(scenario())
        # FIFO: the two earliest dispatches run first.
        assert started == [0, 1, 2, 3]

    def test_release_wakes_the_longest_waiting_subagent(self) -> None:
        async def scenario() -> None:
            scheduler = SubagentScheduler(limit=1)
            await scheduler.acquire()
            order: list[str] = []

            async def waiter(name: str) -> None:
                await scheduler.acquire()
                order.append(name)
                scheduler.release()

            first = asyncio.create_task(waiter("first"))
            second = asyncio.create_task(waiter("second"))
            await asyncio.sleep(0)
            assert scheduler.queued == 2
            scheduler.release()
            await asyncio.gather(first, second)
            assert order == ["first", "second"]

        asyncio.run(scenario())

    def test_cancelled_waiter_does_not_hold_a_slot(self) -> None:
        async def scenario() -> None:
            scheduler = SubagentScheduler(limit=1)
            await scheduler.acquire()
            waiter = asyncio.create_task(scheduler.acquire())
            await asyncio.sleep(0)
            assert scheduler.queued == 1

            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter

            assert scheduler.queued == 0
            scheduler.release()
            assert (scheduler.running, scheduler.queued) == (0, 0)

        asyncio.run(scenario())

    def test_raising_the_limit_drains_the_queue(self) -> None:
        async def scenario() -> None:
            scheduler = SubagentScheduler(limit=1)
            await scheduler.acquire()
            started: list[int] = []

            async def worker(index: int) -> None:
                await scheduler.acquire()
                started.append(index)

            tasks = [asyncio.create_task(worker(i)) for i in range(3)]
            await asyncio.sleep(0)
            assert scheduler.queued == 3

            scheduler.set_limit(4)
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
            assert sorted(started) == [0, 1, 2]
            assert scheduler.queued == 0

            for _ in range(4):
                scheduler.release()

        asyncio.run(scenario())

    def test_shrinking_the_limit_never_preempts_a_running_subagent(self) -> None:
        async def scenario() -> None:
            scheduler = SubagentScheduler(limit=4)
            for _ in range(3):
                await scheduler.acquire()
            scheduler.set_limit(1)
            # Still running above the new cap: the cap only gates new starts.
            assert scheduler.running == 3
            assert not scheduler.admits_now()
            scheduler.release()
            scheduler.release()
            scheduler.release()
            assert scheduler.running == 0

        asyncio.run(scenario())

    def test_reset_drains_waiters(self) -> None:
        async def scenario() -> None:
            scheduler = SubagentScheduler(limit=1)
            await scheduler.acquire()
            waiter = asyncio.create_task(scheduler.acquire())
            await asyncio.sleep(0)
            scheduler.reset()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert (scheduler.running, scheduler.queued) == (0, 0)

        asyncio.run(scenario())
