"""Periodic job definitions wired into the scheduler by `ildrs serve`."""

from __future__ import annotations

import asyncio
import logging

from ildrs.config import get_settings
from ildrs.jobs.scheduler import Scheduler
from ildrs.pipeline.orchestrator import Orchestrator

logger = logging.getLogger("ildrs.jobs")


def register_periodic_jobs(scheduler: Scheduler, orchestrator: Orchestrator) -> None:
    settings = get_settings()

    async def run_verify(cancel: asyncio.Event) -> None:
        await orchestrator.run_stage_guarded("verify", cancel=cancel)

    async def run_rerank(cancel: asyncio.Event) -> None:
        await orchestrator.run_stage_guarded("rate", cancel=cancel)
        await orchestrator.run_stage_guarded("rank", cancel=cancel)

    scheduler.add("verify", run_verify, settings.verify_interval_hours * 3600, jitter=600)
    scheduler.add("rerank", run_rerank, settings.refresh_interval_hours * 3600, jitter=300)
