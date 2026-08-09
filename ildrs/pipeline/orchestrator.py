"""Pipeline orchestrator.

Runs stages with:
- job lifecycle tracking (pending → running → completed/failed/cancelled)
- graceful cancellation via an ``asyncio.Event``
- consistent error capture
- notifications on completion/failure
"""

from __future__ import annotations

import asyncio
import logging

from ildrs.domain.entities import STAGE_NAMES
from ildrs.notifications.notifier import Notifier
from ildrs.pipeline import stages
from ildrs.sources.base import BusinessSource
from ildrs.storage.database import Database
from ildrs.storage.repositories import create_job, finish_job, set_job_running

logger = logging.getLogger("ildrs.pipeline.orchestrator")

_STAGE_FUNCS = {
    "discover": stages.discover_stage,
    "collect": stages.collect_stage,
    "analyze": stages.analyze_stage,
    "rate": stages.rate_stage,
    "rank": stages.rank_stage,
    "verify": stages.verify_stage,
}


class Orchestrator:
    def __init__(self, db: Database, source: BusinessSource, notifier: Notifier) -> None:
        self.db = db
        self.source = source
        self.notifier = notifier

    async def run_stage(self, stage: str, *, cancel: asyncio.Event | None = None, **kwargs) -> dict:
        """Run one stage with full job tracking. Raises on failure."""
        if stage not in STAGE_NAMES:
            raise ValueError(f"unknown stage '{stage}'; use {list(STAGE_NAMES)}")
        func = _STAGE_FUNCS[stage]

        async with self.db.session() as session:
            job = await create_job(session, stage)
            job_id = job.id
            await set_job_running(session, job_id)
            await session.commit()

        logger.info("stage '%s' starting (job=%s)", stage, job_id)
        try:
            if stage in ("discover", "verify", "collect"):
                counts = await func(self.db, self.source, self.notifier, cancel=cancel, **kwargs)
            else:
                counts = await func(self.db, self.notifier, cancel=cancel, **kwargs)

            async with self.db.session() as session:
                await finish_job(session, job_id=job_id, status="completed", counts=counts)
                await session.commit()
            logger.info("stage '%s' completed: %s", stage, counts)
            return {"stage": stage, "job_id": job_id, "status": "completed", "counts": counts}
        except stages.JobCancelled:
            async with self.db.session() as session:
                await finish_job(
                    session, job_id=job_id, status="cancelled", error="cancelled by user"
                )
                await session.commit()
            logger.info("stage '%s' cancelled", stage)
            return {"stage": stage, "job_id": job_id, "status": "cancelled", "counts": {}}
        except Exception as exc:  # noqa: BLE001 - any stage failure is recorded and re-raised
            async with self.db.session() as session:
                await finish_job(session, job_id=job_id, status="failed", error=str(exc))
                await session.commit()
            logger.error("stage '%s' failed: %s", stage, exc)
            await self.notifier.send("error", f"Stage {stage} failed", str(exc))
            raise

    async def run_stage_guarded(
        self, stage: str, *, cancel: asyncio.Event | None = None, **kwargs
    ) -> dict:
        """Run a stage without raising — used by the scheduler/API for background work."""
        try:
            return await self.run_stage(stage, cancel=cancel, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.exception("guarded stage '%s' failed", stage)
            return {"stage": stage, "status": "failed", "error": str(exc)}

    async def run_full_pipeline(self, *, cancel: asyncio.Event | None = None) -> list[dict]:
        """discover → collect → analyze → rate → rank."""
        results = []
        for stage in ("discover", "collect", "analyze", "rate", "rank"):
            if cancel is not None and cancel.is_set():
                logger.info("pipeline interrupted between stages at '%s'", stage)
                break
            results.append(await self.run_stage(stage, cancel=cancel))
        return results
