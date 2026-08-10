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


async def test_rate_stage_persists_expected_value(db, monkeypatch):
    import ildrs.config as config_module
    from ildrs.config import Settings

    monkeypatch.setattr(
        config_module,
        "_settings",
        Settings(
            database_url=f"sqlite+aiosqlite:///{db.engine.url.database}",
            source="fixture",
            ev_prior_probability=0.15,
            ev_deal_value=1000.0,
            ev_cost=50.0,
        ),
    )

    source = FixtureSource()
    await discover_stage(db, source, Notifier(db), limit=3)
    await collect_stage(db, source, Notifier(db))
    await analyze_stage(db, Notifier(db))
    await rate_stage(db, Notifier(db))

    from ildrs.storage.repositories import lead_serialize, list_leads

    async with db.session() as session:
        leads = await list_leads(session, limit=10)
        items = [lead_serialize(x) for x in leads]

    assert len(items) == 3
    for item in items:
        assert item["expected_value"] is not None
        assert item["expected_value"]["ready"] is True
        assert item["expected_value"]["prob_state"] == "estimated"
        assert item["expected_value"]["expected_value"] == pytest.approx(100.0)


async def test_rate_stage_ev_unknown_when_not_configured(db):
    source = FixtureSource()
    await discover_stage(db, source, Notifier(db), limit=3)
    await collect_stage(db, source, Notifier(db))
    await analyze_stage(db, Notifier(db))
    await rate_stage(db, Notifier(db))

    from ildrs.storage.repositories import lead_serialize, list_leads

    async with db.session() as session:
        leads = await list_leads(session, limit=10)
        items = [lead_serialize(x) for x in leads]

    for item in items:
        assert item["expected_value"] is not None
        assert item["expected_value"]["ready"] is False
        assert item["expected_value"]["prob_state"] == "unknown"


async def test_v2_calibrates_and_rates_after_outcomes(db, monkeypatch):
    import ildrs.config as config_module
    from ildrs.config import Settings
    from ildrs.outreach.workflow import OutreachWorkflow
    from ildrs.storage.repositories import list_leads

    monkeypatch.setattr(
        config_module,
        "_settings",
        Settings(
            database_url=f"sqlite+aiosqlite:///{db.engine.url.database}",
            source="fixture",
            rating_model="v2",
            rating_min_samples=3,
        ),
    )

    source = FixtureSource()
    notifier = Notifier(db)
    await discover_stage(db, source, notifier, limit=3)
    await collect_stage(db, source, notifier)
    await analyze_stage(db, notifier)
    await rate_stage(db, notifier)

    # Record enough definitive outcomes for V2 to calibrate.
    workflow = OutreachWorkflow(db)
    async with db.session() as session:
        leads = await list_leads(session, limit=100)
    for i, lead in enumerate(leads):
        opened = await workflow.open(lead_id=lead.id, channel="email", note="test")
        outcome = "interested" if i < 2 else "no_response"
        await workflow.transition(outreach_id=opened.data["id"], status=outcome)
    assert await workflow.outcome_count() >= 3

    result = await rate_stage(db, notifier)
    assert result["model"] == "v2"
    assert result["model_version"] == "v2.0"

    async with db.session() as session:
        re_rated = await list_leads(session, limit=100)
    assert all(lead.model == "v2" for lead in re_rated)
    assert all(lead.model_version == "v2.0" for lead in re_rated)


async def test_v2_falls_back_to_v1_without_enough_outcomes(db, monkeypatch):
    import ildrs.config as config_module
    from ildrs.config import Settings
    from ildrs.storage.repositories import list_leads

    monkeypatch.setattr(
        config_module,
        "_settings",
        Settings(
            database_url=f"sqlite+aiosqlite:///{db.engine.url.database}",
            source="fixture",
            rating_model="v2",
            rating_min_samples=20,
        ),
    )

    source = FixtureSource()
    notifier = Notifier(db)
    await discover_stage(db, source, notifier, limit=3)
    await collect_stage(db, source, notifier)
    await analyze_stage(db, notifier)
    await rate_stage(db, notifier)

    async with db.session() as session:
        leads = await list_leads(session, limit=100)
    # Without outcomes the pipeline must not fake V2: it predicts with V1
    # weights while flagging the fallback.
    assert all(lead.model == "v1" for lead in leads)
    for lead in leads:
        assert lead.features["metadata"].get("fallback")
        assert "not calibrated" in lead.features["metadata"]["fallback"]


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


async def test_state_persists_across_restart(tmp_path):
    """Records written by one Database instance survive a fresh connection.

    Mirrors a real application restart: the same SQLite file is reopened with
    a brand-new Database, and businesses, leads, outcomes, notifications, and
    job history must all still be there.
    """
    from ildrs.outreach.workflow import OutreachWorkflow
    from ildrs.storage.bootstrap import init as init_schema
    from ildrs.storage.database import Database
    from ildrs.storage.repositories import (
        count_outcomes,
        list_businesses,
        list_jobs,
        list_leads,
        list_notifications,
    )

    url = f"sqlite+aiosqlite:///{tmp_path}/restart.db"

    # --- first process: full pipeline + an outcome -----------------------
    db1 = Database(url=url)
    db1.connect()
    await init_schema(db1)
    orchestrator = Orchestrator(db1, FixtureSource(), Notifier(db1))
    await orchestrator.run_full_pipeline(cancel=asyncio.Event())

    async with db1.session() as session:
        leads = await list_leads(session, limit=100)
    assert len(leads) == 3

    workflow = OutreachWorkflow(db1)
    opened = await workflow.open(lead_id=leads[0].id, channel="email", note="restart-test")
    await workflow.transition(outreach_id=opened.data["id"], status="responded")
    assert await workflow.outcome_count() == 1
    await db1.close()

    # --- second process: brand-new connection to the same file -----------
    db2 = Database(url=url)
    db2.connect()
    await init_schema(db2)
    async with db2.session() as session:
        businesses = await list_businesses(session, limit=100)
        leads = await list_leads(session, limit=100)
        outcomes = await count_outcomes(session)
        notifications = await list_notifications(session, limit=100)
        jobs = await list_jobs(session, limit=100)
    await db2.close()

    assert len(businesses) == 3
    assert len(leads) == 3
    assert outcomes == 1
    assert len(notifications) >= 1
    assert any(j.status == "completed" for j in jobs)
