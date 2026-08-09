"""Async task scheduler with graceful shutdown.

Design goals:
- periodic and one-shot tasks, each with its own interval
- tasks cooperate with a cancellation ``asyncio.Event``
- ``stop()`` signals cancellation, waits for tasks to finish (bounded), and
  reports anything that could not be stopped
- no task is killed silently; every shutdown is observable
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

logger = logging.getLogger("ildrs.jobs.scheduler")

TaskFactory = Callable[[asyncio.Event], Awaitable]


@dataclass
class ScheduledTask:
    name: str
    factory: TaskFactory
    interval_seconds: float
    jitter: float = 0.0
    last_run: datetime | None = field(default=None, init=False)


class Scheduler:
    def __init__(self) -> None:
        self._tasks: dict[str, ScheduledTask] = {}
        self._running: set[asyncio.Task] = set()
        self._stop_event = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self.started_at: datetime | None = None

    def add(
        self, name: str, factory: TaskFactory, interval_seconds: float, jitter: float = 0.0
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError(f"interval for '{name}' must be positive")
        self._tasks[name] = ScheduledTask(
            name=name, factory=factory, interval_seconds=interval_seconds, jitter=jitter
        )

    async def start(self) -> None:
        if self._loop_task is not None:
            raise RuntimeError("scheduler already started")
        self.started_at = datetime.now(UTC)
        self._loop_task = asyncio.create_task(self._loop(), name="scheduler-loop")
        logger.info("scheduler started with %d task(s)", len(self._tasks))

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            now = datetime.now(UTC)
            for task in self._tasks.values():
                if self._is_active(task.name):
                    continue  # still running; try again next tick
                if task.last_run is None or (
                    now - task.last_run
                ).total_seconds() >= self._interval_with_jitter(task):
                    task.last_run = now
                    created = asyncio.create_task(self._guard(task), name=f"sched:{task.name}")
                    self._running.add(created)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._tick_seconds())
            except TimeoutError:
                continue

    def _tick_seconds(self) -> float:
        """Wake-up granularity: half the smallest interval (capped at 1s, with a
        50ms floor) so sub-second tasks actually run without a busy loop."""
        if not self._tasks:
            return 1.0
        smallest = min(task.interval_seconds for task in self._tasks.values())
        return min(1.0, max(0.05, smallest / 2))

    @staticmethod
    def _interval_with_jitter(task: ScheduledTask) -> float:
        if task.jitter <= 0:
            return task.interval_seconds
        return task.interval_seconds + random.uniform(0, task.jitter)

    def _is_active(self, name: str) -> bool:
        return any(not t.done() and t.get_name() == f"sched:{name}" for t in self._running)

    async def _guard(self, task: ScheduledTask) -> None:
        try:
            logger.debug("running scheduled task '%s'", task.name)
            await task.factory(self._stop_event)
            logger.debug("scheduled task '%s' finished", task.name)
        except asyncio.CancelledError:
            logger.info("scheduled task '%s' cancelled", task.name)
        except Exception:
            logger.exception("scheduled task '%s' failed", task.name)

    async def stop(self, *, timeout: float = 15.0) -> None:
        """Graceful shutdown: signal, cancel, wait bounded, report."""
        self._stop_event.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

        pending = [t for t in self._running if not t.done()]
        if pending:
            logger.info("stopping scheduler: waiting for %d task(s)", len(pending))
            for task in pending:
                task.cancel()
            done, still = await asyncio.wait(pending, timeout=timeout)
            for task in done:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            for task in still:
                logger.warning(
                    "task '%s' did not stop within %ss; leaving it", task.get_name(), timeout
                )
        self._running.clear()
        self.started_at = None
        logger.info("scheduler stopped")

    @property
    def is_running(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    def summary(self) -> dict:
        return {
            "running": self.is_running,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "tasks": list(self._tasks),
            "active": sorted(t.get_name() for t in self._running if not t.done()),
        }


def next_run_time(interval_seconds: float, last_run: datetime | None = None) -> datetime:
    base = last_run or datetime.now(UTC)
    return base + timedelta(seconds=interval_seconds)
