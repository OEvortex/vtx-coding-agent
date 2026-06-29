from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CronJob:
    name: str
    schedule: str  # cron expression: "*/5 * * * *" or "every 30m"
    prompt: str
    channel: str = ""
    user_id: str = ""
    enabled: bool = True
    one_off: bool = False
    last_run: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class CronJobManager:
    def __init__(self, store_dir: Path | None = None) -> None:
        self._store_dir = store_dir or Path.home() / ".vtx" / "claw" / "cron"
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, CronJob] = {}
        self._running = False
        self._task: asyncio.Task | None = None
        self._executor: Callable[[CronJob], Awaitable[None]] | None = None
        self._load_all()

    def set_executor(self, executor: Callable[[CronJob], Awaitable[None]]) -> None:
        self._executor = executor

    def add_job(self, job: CronJob) -> None:
        self._jobs[job.name] = job
        self._persist(job)
        logger.info("Cron job added: %s (%s)", job.name, job.schedule)

    def remove_job(self, name: str) -> bool:
        if name in self._jobs:
            del self._jobs[name]
            path = self._store_dir / f"{name}.json"
            if path.exists():
                path.unlink()
            return True
        return False

    def list_jobs(self) -> list[CronJob]:
        return list(self._jobs.values())

    def get_job(self, name: str) -> CronJob | None:
        return self._jobs.get(name)

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Cron scheduler started with %d jobs", len(self._jobs))

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("Cron scheduler stopped")

    async def _loop(self) -> None:
        while self._running:
            await asyncio.sleep(30)
            now = time.time()
            for job in list(self._jobs.values()):
                if not job.enabled:
                    continue
                if self._is_due(job, now):
                    job.last_run = now
                    self._persist(job)
                    if self._executor:
                        try:
                            await self._executor(job)
                        except Exception:
                            logger.exception("Cron job %s failed", job.name)
                    if job.one_off:
                        self.remove_job(job.name)

    def _is_due(self, job: CronJob, now: float) -> bool:
        elapsed = now - job.last_run
        schedule = job.schedule.lower().strip()

        m = re.match(r"every\s+(\d+)\s*(s|sec|m|min|h|hr|d|day)", schedule)
        if m:
            num = int(m.group(1))
            unit = m.group(2)
            intervals = {
                "s": 1,
                "sec": 1,
                "m": 60,
                "min": 60,
                "h": 3600,
                "hr": 3600,
                "d": 86400,
                "day": 86400,
            }
            return elapsed >= num * intervals.get(unit, 60)

        parts = schedule.split()
        if len(parts) == 5:
            return self._cron_matches(parts, now, job.last_run)

        return False

    def _cron_matches(self, parts: list[str], now: float, last_run: float) -> bool:
        t = time.localtime(now)
        lt = time.localtime(last_run) if last_run > 0 else None

        checks = [
            (parts[0], t.tm_min, lt.tm_min if lt else None),
            (parts[1], t.tm_hour, lt.tm_hour if lt else None),
            (parts[2], t.tm_mday, lt.tm_mday if lt else None),
            (parts[3], t.tm_mon, lt.tm_mon if lt else None),
            (parts[4], t.tm_wday, lt.tm_wday if lt else None),
        ]

        for pattern, current, _previous in checks:
            if pattern == "*":
                continue
            if pattern.startswith("*/"):
                interval = int(pattern[2:])
                if current % interval != 0:
                    return False
            elif int(pattern) != current:
                return False

        return True

    def _load_all(self) -> None:
        for f in self._store_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                job = CronJob(**data)
                self._jobs[job.name] = job
            except Exception:
                logger.exception("Failed to load cron job %s", f.name)

    def _persist(self, job: CronJob) -> None:
        path = self._store_dir / f"{job.name}.json"
        path.write_text(
            json.dumps(
                {
                    "name": job.name,
                    "schedule": job.schedule,
                    "prompt": job.prompt,
                    "channel": job.channel,
                    "user_id": job.user_id,
                    "enabled": job.enabled,
                    "one_off": job.one_off,
                    "last_run": job.last_run,
                    "metadata": job.metadata,
                },
                indent=2,
            )
        )
