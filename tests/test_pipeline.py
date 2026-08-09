"""End-to-end pipeline tests (Architecture §7)."""

from __future__ import annotations

import asyncio

import pytest

from ildrs.notifications.notifier import Notifier
from ildrs.pipeline.orchestrator import Orchestrator
from ildrs.pipeline.stages import (
    JobCancelled,
    analyze_stage,
    collect_stage,
    discover_stage,
    rate_stage,
    verify_stage,
)
from ildrs.sources.fixture import FixtureSource
from ildrs.storage.repositories import list_jobs

pytestmark = pytest.mark.asyncio


async def _pipeline(db, source=None):
    return Orchestrator(db, source or FixtureSource(), Notifier(db))


async def test_discover_stage_persists_businesses(db):
    result = await discover_stage(db, FixtureSource(), Notifier(db), limit=5)
    # fixture source filters by the default discovery query; 3 rows match it
    assert result["discovered"] == 3


async def test_collect_stage_enriches(db):
    source = FixtureSource()
    await discover_stage(db, source, Notifier(db), limit=3)
    result = await collect_stage(db, source, Notifier(db))
    assert result["collected"] == 3
    assert result["errors"] == 0


async def test_analyze_stage_extracts_features(db):
    source = FixtureSource()
    await discover_stage(db, source, Notifier(db), limit=3)
    await collect_stage(db, source, Notifier(db))
    result = await analyze_stage(db, Notifier(db))
    assert result["analyzed"] == 3
    assert result["valid"] == 3


async def test_rate_stage_creates_leads_with_confidence(db):
    source = FixtureSource()
    await discover_stage(db, source, Notifier(db), limit=3)
    await collect_stage(db, source, Notifier(db))
    await analyze_stage(db, Notifier(db))
    result = await rate_stage(db, Notifier(db))
    assert result["rated"] == 3

    from ildrs.storage.repositories import list_leads

    async with db.session() as session:
        leads = await list_leads(session, limit=10)
    assert len(leads) == 3
    assert all(lead.rating > 0 for lead in leads)
    assert all(lead.confidence > 0 for lead in leads)
    assert all(lead.model == "v1" for lead in leads)


async def test_full_pipeline_ranks_leads(db):
    orchestrator = await _pipeline(db)
    results = await orchestrator.run_full_pipeline(cancel=asyncio.Event())
    stages = [r["stage"] for r in results]
    assert stages == ["discover", "collect", "analyze", "rate", "rank"]
    assert all(r["status"] == "completed" for r in results)

    from ildrs.storage.repositories import list_leads

    async with db.session() as session:
        leads = await list_leads(session, limit=100000)
    assert len(leads) == 3
    ranks = [lead.rank for lead in leads]
    assert ranks == sorted(ranks)
    assert ranks[0] == 1


async def test_rank_is_best_first(db):
    orchestrator = await _pipeline(db)
    await orchestrator.run_full_pipeline(cancel=asyncio.Event())

    from ildrs.storage.repositories import list_leads

    async with db.session() as session:
        leads = await list_leads(session, limit=100000, sort="rank")
    ratings = [lead.rating for lead in leads]
    assert ratings == sorted(ratings, reverse=True)


async def test_verify_stage_recaptures(db):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from ildrs.storage.models import BusinessRow

    source = FixtureSource()
    await discover_stage(db, source, Notifier(db), limit=3)
    await collect_stage(db, source, Notifier(db))

    # collection stamps last_verified_at=now; make the businesses stale
    async with db.session() as session:
        await session.execute(
            update(BusinessRow).values(last_verified_at=datetime.now(UTC) - timedelta(days=30))
        )
        await session.commit()

    result = await verify_stage(db, source, Notifier(db))
    assert result["verified"] == 3


async def test_cancelled_stage_raises_job_cancelled(db):
    source = FixtureSource()
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(JobCancelled):
        await discover_stage(db, source, Notifier(db), cancel=cancel)


async def test_orchestrator_unknown_stage(db):
    orchestrator = await _pipeline(db)
    with pytest.raises(ValueError):
        await orchestrator.run_stage("bogus")


async def test_orchestrator_tracks_job_lifecycle(db):
    orchestrator = await _pipeline(db)
    result = await orchestrator.run_stage("discover")
    assert result["status"] == "completed"
    assert result["job_id"]

    async with db.session() as session:
        jobs = await list_jobs(session, limit=10)
    assert any(j.stage == "discover" and j.status == "completed" for j in jobs)


async def test_guarded_stage_returns_failure_dict(db):
    class ExplodingSource(FixtureSource):
        async def discover(self, query):
            raise RuntimeError("boom")

    orchestrator = await _pipeline(db, source=ExplodingSource())
    result = await orchestrator.run_stage_guarded("discover")
    assert result["status"] == "failed"
    assert "boom" in result["error"]
