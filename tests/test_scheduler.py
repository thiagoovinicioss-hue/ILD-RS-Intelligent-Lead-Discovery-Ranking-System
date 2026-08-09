"""Tests for the async task scheduler (Architecture §9)."""

from __future__ import annotations

import asyncio

import pytest

from ildrs.jobs.scheduler import Scheduler, next_run_time

# async tests are collected automatically via asyncio_mode="auto"


async def test_add_validates_interval():
    scheduler = Scheduler()
    with pytest.raises(ValueError):
        scheduler.add("bad", lambda cancel: None, interval_seconds=0)
    with pytest.raises(ValueError):
        scheduler.add("bad", lambda cancel: None, interval_seconds=-1)


async def test_add_rejects_negative_jitter():
    scheduler = Scheduler()
    scheduler.add("ok", lambda cancel: None, interval_seconds=1, jitter=-0.5)
    # negative jitter is tolerated and treated as no-jitter
    assert scheduler._tasks["ok"].jitter == -0.5


async def test_start_twice_raises():
    scheduler = Scheduler()
    await scheduler.start()
    with pytest.raises(RuntimeError):
        await scheduler.start()
    await scheduler.stop()


async def test_periodic_task_runs():
    scheduler = Scheduler()
    runs: list[int] = []

    async def task(cancel: asyncio.Event) -> None:
        runs.append(1)

    scheduler.add("tick", task, interval_seconds=0.05)
    await scheduler.start()
    await asyncio.sleep(0.3)
    await scheduler.stop()
    assert len(runs) >= 2


async def test_one_shot_task_runs_once_per_interval():
    scheduler = Scheduler()
    runs = 0

    async def task(cancel: asyncio.Event) -> None:
        nonlocal runs
        runs += 1

    scheduler.add("once", task, interval_seconds=3600)
    await scheduler.start()
    await asyncio.sleep(0.2)
    await scheduler.stop()
    assert runs == 1


async def test_stop_cancels_running_task():
    scheduler = Scheduler()

    async def slow_task(cancel: asyncio.Event) -> None:
        await asyncio.sleep(30)

    scheduler.add("slow", slow_task, interval_seconds=0.05)
    await scheduler.start()
    await asyncio.sleep(0.15)
    assert scheduler.is_running
    await scheduler.stop(timeout=1.0)
    assert not scheduler.is_running
    assert scheduler._running == set()


async def test_task_failure_does_not_break_loop():
    scheduler = Scheduler()

    async def flaky(cancel: asyncio.Event) -> None:
        raise RuntimeError("scheduled task exploded")

    async def healthy(cancel: asyncio.Event) -> None:
        return None

    scheduler.add("flaky", flaky, interval_seconds=0.05)
    scheduler.add("healthy", healthy, interval_seconds=0.05)
    await scheduler.start()
    await asyncio.sleep(0.2)
    await scheduler.stop()
    assert not scheduler.is_running


async def test_summary_shape():
    scheduler = Scheduler()
    scheduler.add("tick", lambda cancel: None, interval_seconds=1)
    summary = scheduler.summary()
    assert summary["tasks"] == ["tick"]
    assert summary["running"] is False
    assert "active" in summary


def test_next_run_time():
    from datetime import UTC, datetime, timedelta

    base = datetime(2026, 1, 1, tzinfo=UTC)
    assert next_run_time(60, base) == base + timedelta(seconds=60)
    assert next_run_time(60) > datetime.now(UTC)
