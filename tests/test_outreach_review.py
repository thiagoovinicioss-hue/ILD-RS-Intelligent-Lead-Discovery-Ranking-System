"""Tests for the outreach review queue, message generation and monitoring
(Architecture §5/§6)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from ildrs.domain.provenance import ProvenanceMap
from ildrs.notifications.notifier import Notifier
from ildrs.outreach.messages import OutreachMessageGenerator
from ildrs.outreach.monitoring import ResponseMonitor
from ildrs.outreach.review import ReviewWorkflow
from ildrs.storage.repositories import (
    create_outreach,
    list_leads,
    upsert_business,
    upsert_lead,
)
from tests.conftest import make_business


async def _seed_lead(db, *, rating: float = 50.0) -> str:
    import uuid

    business = make_business(
        name="Verified Plumbing Co", rating=4.6, reviews=210, external_id=f"fix-{uuid.uuid4()}"
    )
    async with db.session() as session:
        b = await upsert_business(session, business)
        lead = await upsert_lead(
            session,
            business_id=b.id,
            rating=rating,
            confidence=0.8,
            model="v1",
            model_version="v1.2",
            features={},
        )
        await session.commit()
        return lead.id


async def _prepare_review(db, *, rating: float = 50.0):
    lead_id = await _seed_lead(db, rating=rating)
    workflow = ReviewWorkflow(db)
    result = await workflow.prepare(lead_id=lead_id)
    assert result.ok
    return workflow, lead_id, result.data["id"]


async def _seed_sent(db, *, status: str = "sent") -> str:
    lead_id = await _seed_lead(db)
    async with db.session() as session:
        row = await create_outreach(
            session,
            lead_id=lead_id,
            channel="email",
            status="sent",
            message="Hi",
            reason="test",
            review_status="approved",
            sent_status=status,
        )
        await session.commit()
        return row.id


# -- review queue lifecycle -------------------------------------------------


async def test_prepare_enqueues_pending_item(db):
    workflow, lead_id, outreach_id = await _prepare_review(db)
    pending = await workflow.list_pending()
    assert len(pending) == 1
    item = pending[0]
    assert item["id"] == outreach_id
    assert item["review_status"] == "pending"
    assert item["business_name"] == "Verified Plumbing Co"
    assert item["rating"] == 50.0
    assert "Verified Plumbing Co" in item["message"]


async def test_prepare_is_idempotent_per_lead(db):
    lead_id = await _seed_lead(db)
    workflow = ReviewWorkflow(db)
    first = await workflow.prepare(lead_id=lead_id)
    second = await workflow.prepare(lead_id=lead_id)
    assert first.ok and second.ok
    assert second.data["duplicate"] is True
    assert second.data["id"] == first.data["id"]
    assert len(await workflow.list_pending()) == 1


async def test_approve_queues_message_for_sending(db):
    workflow, _, outreach_id = await _prepare_review(db)
    result = await workflow.approve(outreach_id=outreach_id)
    assert result.ok
    item = (await workflow.list_all())[0]
    assert item["review_status"] == "approved"
    assert item["sent_status"] == "queued"


async def test_approve_rejects_empty_message(db):
    lead_id = await _seed_lead(db)
    workflow = ReviewWorkflow(db)
    async with db.session() as session:
        row = await create_outreach(
            session,
            lead_id=lead_id,
            channel="email",
            status="queued",
            message="   ",
            reason="",
            review_status="pending",
            sent_status="draft",
        )
        await session.commit()
        outreach_id = row.id
    result = await workflow.approve(outreach_id=outreach_id)
    assert not result.ok


async def test_edit_updates_message_and_reason(db):
    workflow, _, outreach_id = await _prepare_review(db)
    result = await workflow.edit(
        outreach_id=outreach_id, message="Edited hello", reason="rewritten"
    )
    assert result.ok
    item = (await workflow.list_all())[0]
    assert item["message"] == "Edited hello"
    assert item["reason"] == "rewritten"


async def test_reject_closes_draft_forever(db):
    workflow, _, outreach_id = await _prepare_review(db)
    result = await workflow.reject(outreach_id=outreach_id, note="too pushy")
    assert result.ok
    item = (await workflow.list_all())[0]
    assert item["review_status"] == "rejected"
    assert "Rejected: too pushy" in item["note"]
    denied = await workflow.approve(outreach_id=outreach_id)
    assert not denied.ok


async def test_mark_sent_requires_approval(db):
    workflow, _, pending_id = await _prepare_review(db)
    denied = await workflow.mark_sent(outreach_id=pending_id)
    assert not denied.ok

    approved_id = pending_id
    await workflow.approve(outreach_id=approved_id)
    ok = await workflow.mark_sent(outreach_id=approved_id)
    assert ok.ok and ok.data["sent_status"] == "sent"
    # second mark is refused
    again = await workflow.mark_sent(outreach_id=approved_id)
    assert not again.ok


async def test_prepare_pending_skips_unrated_leads(db):
    await _seed_lead(db, rating=0)
    workflow = ReviewWorkflow(db)
    result = await workflow.prepare_pending()
    assert result.data["prepared"] == 0
    assert await workflow.count_pending() == 0


async def test_prepare_pending_creates_drafts(db):
    await _seed_lead(db, rating=60.0)
    workflow = ReviewWorkflow(db)
    result = await workflow.prepare_pending()
    assert result.data["prepared"] == 1
    assert await workflow.count_pending() == 1


async def test_count_pending_matches_queue(db):
    await _prepare_review(db)
    await _prepare_review(db)
    workflow = ReviewWorkflow(db)
    assert await workflow.count_pending() == 2


# -- message generation guardrails ------------------------------------------


async def test_generator_states_only_verified_facts(db):
    await _seed_lead(db)
    async with db.session() as session:
        leads = await list_leads(session, limit=1)
        lead = leads[0]

    draft = OutreachMessageGenerator().generate(lead)
    assert "4.6/5 rating" in draft.message
    assert "210 reviews" in draft.message
    # the reason must be present and the suggestion clearly labeled
    assert draft.reason.startswith("lead rating")
    assert "Suggestion:" in draft.message


async def test_generator_never_fabricates_facts(db):
    await _seed_lead(db)
    business = make_business(name="Mystery Co", rating=None, reviews=0, has_website=False)
    async with db.session() as session:
        leads = await list_leads(session, limit=1)
        lead = leads[0]

    draft = OutreachMessageGenerator().generate_for(business, lead)
    assert "0 reviews" not in draft.message
    assert "rating" not in draft.message.lower()
    # unverified facts must not appear as observations
    assert "Observed:" not in draft.message


async def test_generator_skips_unverified_provenance(db):
    await _seed_lead(db)
    from ildrs.domain.entities import Business

    business = Business(
        source="fixture",
        external_id="u",
        name="Guessed Co",
        category="plumber",
        google_rating=3.0,
        review_count=5,
        business_status="OPERATIONAL",
        provenance=ProvenanceMap(),  # no verified provenance at all
    )
    async with db.session() as session:
        leads = await list_leads(session, limit=1)
        lead = leads[0]

    draft = OutreachMessageGenerator().generate_for(business, lead)
    assert "3.0/5" not in draft.message
    assert "5 reviews" not in draft.message


async def test_generator_message_contains_suggestion_label(db):
    await _seed_lead(db)
    async with db.session() as session:
        leads = await list_leads(session, limit=1)
        lead = leads[0]

    draft = OutreachMessageGenerator().generate(lead)
    assert any("Suggestion:" in line for line in draft.message.splitlines())


# -- response monitoring ----------------------------------------------------


async def test_monitor_unavailable_without_integration(db, monkeypatch):
    import ildrs.config as config_module

    settings = config_module.get_settings()
    monkeypatch.setattr(settings, "outreach_monitor_source", "none")

    monitor = ResponseMonitor(db)
    result = await monitor.run_once()
    assert result["checked"] == 0
    assert result["sources"]["none"]["status"] == "unavailable"
    rows = await monitor.status()
    assert rows and rows[0]["configured"] is False


async def test_monitor_stamps_sent_items_even_when_unconfigured(db, monkeypatch):
    import ildrs.config as config_module

    settings = config_module.get_settings()
    monkeypatch.setattr(settings, "outreach_monitor_source", "none")
    outreach_id = await _seed_sent(db)

    monitor = ResponseMonitor(db)
    await monitor.run_once()
    async with db.session() as session:
        from ildrs.storage.repositories import get_outreach

        row = await get_outreach(session, outreach_id)
        assert row.last_checked_at is not None
        assert row.next_check_at is not None


async def test_monitor_unknown_source_reports_unavailable(db, monkeypatch):
    import ildrs.config as config_module

    settings = config_module.get_settings()
    monkeypatch.setattr(settings, "outreach_monitor_source", "not_a_source")
    monitor = ResponseMonitor(db)
    result = await monitor.run_once()
    assert result["sources"]["not_a_source"]["status"] == "unavailable"


async def test_monitor_notifies_once_when_unconfigured(db, monkeypatch):
    import ildrs.config as config_module

    settings = config_module.get_settings()
    monkeypatch.setattr(settings, "outreach_monitor_source", "none")

    notifier = Notifier(db)
    notifier.send = AsyncMock()
    monitor = ResponseMonitor(db, notifier)

    await monitor.run_once()
    assert notifier.send.call_count == 1
    # second pass: no repeat notification
    await monitor.run_once()
    assert notifier.send.call_count == 1


async def test_monitor_operational_source_applies_responses(db, monkeypatch):
    import ildrs.config as config_module

    settings = config_module.get_settings()
    monkeypatch.setattr(settings, "outreach_monitor_source", "google_places")

    monitor = ResponseMonitor(db)
    result = await monitor.run_once()
    # google places has no credentials by default → unavailable, never fabricates a check
    assert result["sources"]["google_places"]["configured"] is False
    assert result["sources"]["google_places"]["status"] == "unavailable"
