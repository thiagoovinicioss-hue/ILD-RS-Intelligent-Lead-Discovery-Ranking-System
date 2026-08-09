"""Periodic job definitions wired into the scheduler by `ildrs serve`."""

from __future__ import annotations

import asyncio
import logging

from ildrs.config import get_settings
from ildrs.jobs.scheduler import Scheduler
from ildrs.outreach.monitoring import ResponseMonitor
from ildrs.outreach.review import ReviewWorkflow
from ildrs.pipeline.orchestrator import Orchestrator

logger = logging.getLogger("ildrs.jobs")


def register_periodic_jobs(
    scheduler: Scheduler,
    orchestrator: Orchestrator,
    review: ReviewWorkflow | None = None,
    monitor: ResponseMonitor | None = None,
) -> None:
    settings = get_settings()

    async def run_verify(cancel: asyncio.Event) -> None:
        await orchestrator.run_stage_guarded("verify", cancel=cancel)

    async def run_rerank(cancel: asyncio.Event) -> None:
        await orchestrator.run_stage_guarded("rate", cancel=cancel)
        await orchestrator.run_stage_guarded("rank", cancel=cancel)

    scheduler.add("verify", run_verify, settings.verify_interval_hours * 3600, jitter=600)
    scheduler.add("rerank", run_rerank, settings.refresh_interval_hours * 3600, jitter=300)

    if review is not None and settings.outreach_auto_prepare:

        async def run_prepare(cancel: asyncio.Event) -> None:
            await review.prepare_pending()

        scheduler.add("outreach-prepare", run_prepare, 3600, jitter=300)

    if monitor is not None:

        async def run_monitor(cancel: asyncio.Event) -> None:
            await monitor.run_once(cancel=cancel)

        scheduler.add(
            "outreach-monitor",
            run_monitor,
            settings.outreach_monitor_interval_minutes * 60,
            jitter=60,
        )
