"""API smoke tests (Architecture §8) via the FastAPI TestClient."""

from __future__ import annotations

import pytest

import ildrs.config as config_module
from ildrs.config import Settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/api.db",
        source="fixture",
        discovery_limit=5,
        ev_prior_probability=0.15,
        ev_deal_value=1000.0,
        ev_cost=50.0,
    )
    monkeypatch.setattr(config_module, "_settings", settings)

    from fastapi.testclient import TestClient

    from ildrs.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_status_reports_model(client):
    res = client.get("/api/v1/system/status")
    assert res.status_code == 200
    body = res.json()
    assert body["rating"]["model"] == "v1"
    assert body["system"]["status"] in ("running", "idle")


def test_dashboard_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "ILD-RS" in res.text
    assert 'id="leads-body"' in res.text
    assert 'id="lead-detail"' in res.text


def test_dashboard_assets_relative(client):
    res = client.get("/")
    assert 'href="css/theme.css"' in res.text
    assert 'src="js/app.js"' in res.text


def test_status_v2_readiness_reflects_outcome_count(tmp_path, monkeypatch):
    import asyncio

    import ildrs.config as config_module
    from ildrs.config import Settings
    from ildrs.notifications.notifier import Notifier
    from ildrs.outreach.workflow import OutreachWorkflow
    from ildrs.pipeline.stages import analyze_stage, collect_stage, discover_stage, rate_stage
    from ildrs.sources.fixture import FixtureSource
    from ildrs.storage.bootstrap import init as init_schema
    from ildrs.storage.database import Database
    from ildrs.storage.repositories import list_leads

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/api.db",
        source="fixture",
        rating_model="v2",
        rating_min_samples=3,
    )
    monkeypatch.setattr(config_module, "_settings", settings)

    async def run_pipeline():
        db = Database()
        db.connect()
        await init_schema(db)
        source = FixtureSource()
        notifier = Notifier(db)
        await discover_stage(db, source, notifier, limit=3)
        await collect_stage(db, source, notifier)
        await analyze_stage(db, notifier)
        await rate_stage(db, notifier)

        workflow = OutreachWorkflow(db)
        async with db.session() as session:
            leads = await list_leads(session, limit=100)
        for i, lead in enumerate(leads):
            opened = await workflow.open(lead_id=lead.id, channel="email", note="t")
            await workflow.transition(
                outreach_id=opened.data["id"],
                status="interested" if i < 2 else "no_response",
            )
        await db.close()

    asyncio.run(run_pipeline())

    from fastapi.testclient import TestClient

    from ildrs.api.app import create_app

    with TestClient(create_app()) as c:
        res = c.get("/api/v1/system/status")
        assert res.status_code == 200
        status = res.json()["rating"]["model_status"]
        assert status["name"] == "v2"
        assert status["status"] == "calibrated"


def test_status_v2_awaiting_data_without_outcomes(tmp_path, monkeypatch):
    import ildrs.config as config_module
    from ildrs.config import Settings

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/api2.db",
        source="fixture",
        rating_model="v2",
        rating_min_samples=20,
    )
    monkeypatch.setattr(config_module, "_settings", settings)

    from fastapi.testclient import TestClient

    from ildrs.api.app import create_app

    with TestClient(create_app()) as c:
        res = c.get("/api/v1/system/status")
        assert res.status_code == 200
        status = res.json()["rating"]["model_status"]
        assert status["name"] == "v2"
        assert "awaiting data" in status["status"]


def test_config_exposes_ev_values(client):
    res = client.get("/api/v1/config")
    assert res.status_code == 200
    body = res.json()
    assert body["ev_deal_value"] == 1000.0
    assert body["ev_cost"] == 50.0
    assert "api_key" not in body


def test_leads_expose_expected_value_after_pipeline(client, tmp_path):
    import asyncio

    from ildrs.notifications.notifier import Notifier
    from ildrs.pipeline.stages import analyze_stage, collect_stage, discover_stage, rate_stage
    from ildrs.sources.fixture import FixtureSource
    from ildrs.storage.bootstrap import init as init_schema
    from ildrs.storage.database import Database

    async def run_pipeline():
        db = Database()
        db.connect()
        await init_schema(db)
        source = FixtureSource()
        notifier = Notifier(db)
        await discover_stage(db, source, notifier, limit=3)
        await collect_stage(db, source, notifier)
        await analyze_stage(db, notifier)
        await rate_stage(db, notifier)
        await db.close()

    asyncio.run(run_pipeline())

    res = client.get("/api/v1/leads?limit=10&sort=rank")
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 3
    for item in items:
        assert item["expected_value"] is not None
        assert item["expected_value"]["ready"] is True
        assert item["expected_value"]["expected_value"] == pytest.approx(100.0)


def _run_discovery_and_rank(tmp_path):
    """Run discover→collect→analyze→rate→rank against an isolated DB."""
    import asyncio

    from ildrs.notifications.notifier import Notifier
    from ildrs.pipeline.stages import (
        analyze_stage,
        collect_stage,
        discover_stage,
        rank_stage,
        rate_stage,
    )
    from ildrs.sources.fixture import FixtureSource
    from ildrs.storage.bootstrap import init as init_schema
    from ildrs.storage.database import Database

    async def run():
        db = Database()
        db.connect()
        await init_schema(db)
        source = FixtureSource()
        notifier = Notifier(db)
        await discover_stage(db, source, notifier, limit=3)
        await collect_stage(db, source, notifier)
        await analyze_stage(db, notifier)
        await rate_stage(db, notifier)
        await rank_stage(db, notifier)
        await db.close()

    asyncio.run(run())


def _prepare_first_lead(tmp_path, client) -> str:
    """Run the pipeline and enqueue one draft for review; return its outreach id."""

    _run_discovery_and_rank(tmp_path)

    res = client.get("/api/v1/leads?limit=1&sort=rank")
    lead_id = res.json()["items"][0]["id"]
    res = client.post(f"/api/v1/leads/{lead_id}/outreach/prepare", json={"channel": "email"})
    assert res.status_code == 200
    return res.json()["id"]


def test_review_queue_reports_pending_drafts(tmp_path, monkeypatch):
    import ildrs.config as config_module
    from ildrs.config import Settings

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/review.db",
        source="fixture",
        discovery_limit=5,
    )
    monkeypatch.setattr(config_module, "_settings", settings)

    from fastapi.testclient import TestClient

    from ildrs.api.app import create_app

    with TestClient(create_app()) as c:
        _run_discovery_and_rank(tmp_path)
        res = c.get("/api/v1/outreach/pending")
        assert res.status_code == 200
        assert res.json()["items"] == []

        lead_id = c.get("/api/v1/leads?limit=1&sort=rank").json()["items"][0]["id"]
        prepped = c.post(f"/api/v1/leads/{lead_id}/outreach/prepare", json={"channel": "email"})
        assert prepped.status_code == 200
        assert prepped.json()["review_status"] == "pending"

        queue = c.get("/api/v1/outreach/pending").json()
        assert len(queue["items"]) == 1
        item = queue["items"][0]
        assert item["review_status"] == "pending"
        assert item["message"]
        assert item["rating"] is not None
        assert "Suggestion:" in item["message"]


def test_review_approve_then_send(tmp_path, monkeypatch):
    import ildrs.config as config_module
    from ildrs.config import Settings

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/approve.db",
        source="fixture",
        discovery_limit=5,
    )
    monkeypatch.setattr(config_module, "_settings", settings)

    from fastapi.testclient import TestClient

    from ildrs.api.app import create_app

    with TestClient(create_app()) as c:
        outreach_id = _prepare_first_lead(tmp_path, c)

        denied = c.post(f"/api/v1/outreach/{outreach_id}/send")
        assert denied.status_code == 400

        approved = c.post(f"/api/v1/outreach/{outreach_id}/approve", json={"note": "ok"})
        assert approved.status_code == 200
        assert approved.json()["review_status"] == "approved"

        sent = c.post(f"/api/v1/outreach/{outreach_id}/send")
        assert sent.status_code == 200
        assert sent.json()["sent_status"] == "sent"

        detail = c.get(f"/api/v1/outreach/{outreach_id}").json()
        assert detail["review_status"] == "approved"
        assert detail["sent_status"] == "sent"


def test_review_reject_blocks_send(tmp_path, monkeypatch):
    import ildrs.config as config_module
    from ildrs.config import Settings

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/reject.db",
        source="fixture",
        discovery_limit=5,
    )
    monkeypatch.setattr(config_module, "_settings", settings)

    from fastapi.testclient import TestClient

    from ildrs.api.app import create_app

    with TestClient(create_app()) as c:
        outreach_id = _prepare_first_lead(tmp_path, c)
        rejected = c.post(f"/api/v1/outreach/{outreach_id}/reject", json={"note": "no"})
        assert rejected.status_code == 200

        denied = c.post(f"/api/v1/outreach/{outreach_id}/send")
        assert denied.status_code == 400


def test_monitoring_endpoints_report_status(tmp_path, monkeypatch):
    import ildrs.config as config_module
    from ildrs.config import Settings

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/monitor.db",
        source="fixture",
        discovery_limit=5,
    )
    monkeypatch.setattr(config_module, "_settings", settings)

    from fastapi.testclient import TestClient

    from ildrs.api.app import create_app

    with TestClient(create_app()) as c:
        status = c.get("/api/v1/outreach/monitoring").json()
        assert "sources" in status

        ran = c.post("/api/v1/outreach/monitoring/run")
        assert ran.status_code == 200
        assert ran.json()["sources"]["none"]["status"] == "unavailable"

        # system status exposes review + monitoring blocks
        sys_status = c.get("/api/v1/system/status").json()
        assert "review_queue" in sys_status
        assert "monitoring" in sys_status
        assert sys_status["monitoring"]["configured"] is False
