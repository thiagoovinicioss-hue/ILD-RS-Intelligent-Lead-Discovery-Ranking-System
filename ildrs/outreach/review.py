"""Outreach review workflow.

A proposed outreach message must pass **human review** before it can be sent.
This module owns the review queue lifecycle:

- ``prepare``  generate a verified-facts draft and enqueue it (PENDING REVIEW)
- ``approve``  human approves → message becomes sendable (queued)
- ``edit``     human edits message/reason → re-enqueued as edited
- ``reject``   human rejects → draft is closed, never sent

``mark_sent`` records that a channel actually delivered an approved message.
No automated broadcast is ever performed here.
"""

from __future__ import annotations

import logging

from ildrs.domain.entities import OUTREACH_CHANNELS
from ildrs.notifications.notifier import Notifier
from ildrs.outreach.messages import OutreachMessageGenerator
from ildrs.outreach.workflow import TransitionResult
from ildrs.storage.database import Database
from ildrs.storage.repositories import (
    create_outreach,
    edit_outreach,
    get_lead,
    get_outreach,
    list_outreach,
    mark_outreach_sent,
    pending_review_items,
    set_outreach_review_status,
)

logger = logging.getLogger("ildrs.outreach.review")


class ReviewError(ValueError):
    pass


class ReviewWorkflow:
    def __init__(
        self,
        db: Database,
        notifier: Notifier | None = None,
        generator: OutreachMessageGenerator | None = None,
    ) -> None:
        self.db = db
        self.notifier = notifier
        self.generator = generator or OutreachMessageGenerator()

    # -- queue -------------------------------------------------------------

    async def prepare(self, *, lead_id: str, channel: str = "") -> TransitionResult:
        """Generate a verified-facts draft for a lead and enqueue for review.

        Idempotent: a lead with a pending draft returns that draft instead of
        creating a second one.
        """
        if channel and channel not in OUTREACH_CHANNELS:
            return TransitionResult(
                False, f"unknown channel '{channel}'; use {list(OUTREACH_CHANNELS)}"
            )
        async with self.db.session() as session:
            lead = await get_lead(session, lead_id)
            if lead is None:
                return TransitionResult(False, f"lead '{lead_id}' not found")
            existing = await self._pending_for_lead(session, lead_id)
            if existing is not None:
                return TransitionResult(True, data={"id": existing.id, "duplicate": True})

            draft = self.generator.generate(lead)
            row = await create_outreach(
                session,
                lead_id=lead_id,
                channel=channel or "email",
                status="queued",
                message=draft.message,
                reason=draft.reason,
                review_status="pending",
                sent_status="draft",
            )
            await session.commit()
            if row is None:
                return TransitionResult(False, f"lead '{lead_id}' not found")

        await self._notify_prepare(lead_id, draft.message, lead.rating)
        return TransitionResult(
            True,
            data={
                "id": row.id,
                "lead_id": lead_id,
                "channel": row.channel,
                "review_status": "pending",
            },
        )

    async def prepare_pending(self, *, limit: int = 50) -> TransitionResult:
        """Auto-prepare drafts for rated leads that have no draft yet.

        Runs as a background job; never sends anything. Skips leads that
        already have an outreach record (any status) to avoid spam and dupes.
        """
        from ildrs.storage.repositories import leads_without_outreach

        prepared = 0
        async with self.db.session() as session:
            leads = await leads_without_outreach(session, limit=limit)
            for lead in leads:
                if lead.rating <= 0:
                    continue
                draft = self.generator.generate(lead)
                row = await create_outreach(
                    session,
                    lead_id=lead.id,
                    channel="email",
                    status="queued",
                    message=draft.message,
                    reason=draft.reason,
                    review_status="pending",
                    sent_status="draft",
                )
                if row is not None:
                    prepared += 1
            await session.commit()
        if prepared:
            logger.info("prepared %d outreach draft(s) for review", prepared)
        return TransitionResult(True, data={"prepared": prepared})

    async def list_pending(self, *, limit: int = 100) -> list[dict]:
        """Review queue: proposed messages awaiting human decision."""
        async with self.db.session() as session:
            rows = await pending_review_items(session, limit=limit)
            return [_review_item(r) for r in rows]

    async def list_all(self, *, limit: int = 100, review_status: str | None = None) -> list[dict]:
        async with self.db.session() as session:
            rows = await list_outreach(session, limit=limit, review_status=review_status)
            return [_review_item(r) for r in rows]

    async def count_pending(self) -> int:
        async with self.db.session() as session:
            rows = await pending_review_items(session, limit=100000)
            return len(rows)

    # -- decisions ---------------------------------------------------------

    async def approve(self, *, outreach_id: str) -> TransitionResult:
        """Human approval: draft becomes sendable (queued)."""
        async with self.db.session() as session:
            row = await get_outreach(session, outreach_id)
            if row is None:
                return TransitionResult(False, f"outreach '{outreach_id}' not found")
            if row.review_status == "rejected":
                return TransitionResult(False, "rejected draft cannot be approved; edit it first")
            if not row.message.strip():
                return TransitionResult(False, "cannot approve an empty message")
            row.review_status = "approved"
            row.sent_status = "queued"
            row.note = (row.note + "\nApproved by reviewer.").strip()
            await session.commit()
        return TransitionResult(True, data={"id": outreach_id, "review_status": "approved"})

    async def edit(self, *, outreach_id: str, message: str, reason: str = "") -> TransitionResult:
        """Human edits the proposed message, then it is queued for sending."""
        message = (message or "").strip()
        if not message:
            return TransitionResult(False, "edited message cannot be empty")
        async with self.db.session() as session:
            row = await edit_outreach(session, outreach_id, message=message, reason=reason.strip())
            if row is None:
                return TransitionResult(False, f"outreach '{outreach_id}' not found")
            await session.commit()
        return TransitionResult(True, data={"id": outreach_id, "review_status": "edited"})

    async def reject(self, *, outreach_id: str, note: str = "") -> TransitionResult:
        """Human rejection: the draft is closed and will never be sent."""
        async with self.db.session() as session:
            row = await set_outreach_review_status(session, outreach_id, "rejected")
            if row is None:
                return TransitionResult(False, f"outreach '{outreach_id}' not found")
            if note:
                row.note = (row.note + f"\nRejected: {note}").strip()
            row.sent_status = "draft"
            await session.commit()
        return TransitionResult(True, data={"id": outreach_id, "review_status": "rejected"})

    async def mark_sent(self, *, outreach_id: str) -> TransitionResult:
        """Record that a channel actually delivered the approved message."""
        async with self.db.session() as session:
            row = await get_outreach(session, outreach_id)
            if row is None:
                return TransitionResult(False, f"outreach '{outreach_id}' not found")
            if row.review_status not in ("approved", "edited"):
                return TransitionResult(
                    False,
                    f"cannot send without approval (review_status={row.review_status})",
                )
            if row.sent_status == "sent":
                return TransitionResult(False, "already sent")
            await mark_outreach_sent(session, outreach_id)
            await session.commit()
        return TransitionResult(True, data={"id": outreach_id, "sent_status": "sent"})

    # -- helpers -----------------------------------------------------------

    async def _pending_for_lead(self, session, lead_id: str):
        for row in await pending_review_items(session, limit=100000):
            if row.lead_id == lead_id:
                return row
        return None

    async def _notify_prepare(self, lead_id: str, message: str, rating: float) -> None:
        if self.notifier is None:
            return
        from ildrs.config import get_settings

        threshold = get_settings().outreach_high_value_rating
        if rating is not None and rating >= threshold:
            await self.notifier.send(
                "info",
                "High-value lead queued for review",
                f"Lead {lead_id} (rating {rating:.1f}) now has a pending outreach draft.",
            )


def _review_item(row) -> dict:
    """Serialize an outreach row into a review-queue item (human-readable)."""
    lead = row.lead
    business = lead.business if lead is not None else None
    return {
        "id": row.id,
        "lead_id": row.lead_id,
        "business_name": business.name if business else None,
        "business_source": business.source if business else None,
        "business_category": business.category if business else None,
        "business_address": business.address if business else None,
        "business_website": business.website if business else None,
        "business_phone": business.phone if business else None,
        "rating": lead.rating if lead is not None else None,
        "confidence": lead.confidence if lead is not None else None,
        "rank": lead.rank if lead is not None else None,
        "model": lead.model if lead is not None else None,
        "reason": row.reason,
        "message": row.message,
        "channel": row.channel,
        "review_status": row.review_status,
        "sent_status": row.sent_status,
        "note": row.note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
