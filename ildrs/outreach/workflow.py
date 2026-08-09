"""Outreach workflow.

Drives the manual-review → outreach → outcome lifecycle. Every status
transition that represents a definitive outcome is recorded into
``historical_outcomes`` so the rating model can learn from real results.
"""

from __future__ import annotations

from dataclasses import dataclass

from ildrs.domain.entities import (
    OUTCOME_POSITIVE,
    OUTREACH_CHANNELS,
    OUTREACH_STATUSES,
)
from ildrs.storage.database import Database
from ildrs.storage.repositories import (
    count_outcomes,
    create_outreach,
    get_lead,
    get_outreach,
    list_outcomes,
    outreach_for_lead,
    record_outcome,
    set_lead_status,
    update_outreach_status,
)

LEAD_STATUS_BY_OUTCOME = {
    "responded": "contacted",
    "interested": "contacted",
    "converted": "won",
    "no_response": "lost",
    "declined": "lost",
}


@dataclass
class TransitionResult:
    ok: bool
    error: str = ""
    data: dict | None = None


class OutreachError(ValueError):
    pass


class OutreachWorkflow:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def open(self, *, lead_id: str, channel: str, note: str = "") -> TransitionResult:
        if channel not in OUTREACH_CHANNELS:
            return TransitionResult(
                False, f"unknown channel '{channel}'; use {list(OUTREACH_CHANNELS)}"
            )
        async with self.db.session() as session:
            row = await create_outreach(
                session,
                lead_id=lead_id,
                channel=channel,
                status="queued",
                note=note,
                review_status="approved",
                sent_status="queued",
            )
            if row is None:
                return TransitionResult(False, f"lead '{lead_id}' not found")
            await set_lead_status(session, lead_id, "outreach")
            await session.commit()
            return TransitionResult(
                True, data={"id": row.id, "lead_id": lead_id, "channel": channel}
            )

    async def transition(self, *, outreach_id: str, status: str) -> TransitionResult:
        if status not in OUTREACH_STATUSES:
            return TransitionResult(
                False, f"unknown outreach status '{status}'; use {list(OUTREACH_STATUSES)}"
            )
        async with self.db.session() as session:
            row = await get_outreach(session, outreach_id)
            if row is None:
                return TransitionResult(False, f"outreach '{outreach_id}' not found")
            updated = await update_outreach_status(session, outreach_id, status)
            if updated is None:
                return TransitionResult(False, f"outreach '{outreach_id}' not found")

            lead = await get_lead(session, updated.lead_id)
            if lead is not None:
                if status in OUTCOME_POSITIVE or status in {"no_response", "declined"}:
                    await self._record_outcome(session, lead, status)
                new_lead_status = LEAD_STATUS_BY_OUTCOME.get(status)
                if new_lead_status:
                    await set_lead_status(session, lead.id, new_lead_status)
            await session.commit()
            return TransitionResult(True, data={"id": outreach_id, "status": status})

    async def _record_outcome(self, session, lead, outcome: str) -> None:
        positive = outcome in OUTCOME_POSITIVE
        await record_outcome(
            session,
            business_id=lead.business_id,
            lead_id=lead.id,
            outcome=outcome,
            outcome_value=1 if positive else 0,
            features=_feature_snapshot(lead.features),
        )

    async def history(self, *, lead_id: str) -> list[dict]:
        async with self.db.session() as session:
            rows = await outreach_for_lead(session, lead_id)
            from ildrs.storage.repositories import outreach_serialize

            return [outreach_serialize(r) for r in rows]

    async def outcome_samples(self, *, limit: int = 2000) -> list:
        async with self.db.session() as session:
            rows = await list_outcomes(session, limit=limit)
            return [outcome_to_sample(r) for r in rows]

    async def outcome_count(self) -> int:
        async with self.db.session() as session:
            return await count_outcomes(session)


def outcome_to_sample(row) -> dict:
    features = {}
    stored = row.features
    if isinstance(stored, dict):
        for key, value in stored.items():
            if isinstance(value, dict):
                features[key] = value.get("value")
            else:
                features[key] = value
    return {"features": features, "outcome_value": row.outcome_value}


def _feature_snapshot(lead_features) -> dict:
    """Per-feature normalized values for the historical outcome snapshot.

    ``lead.features`` stores the full RatingResult (with a nested
    ``breakdown``); the calibration path only needs the per-feature
    ``value``/``normalized`` entries, keyed by feature name.
    """
    if not isinstance(lead_features, dict):
        return {}
    breakdown = lead_features.get("breakdown")
    if isinstance(breakdown, dict):
        return {
            key: data.get("normalized", data.get("value"))
            for key, data in breakdown.items()
            if isinstance(data, dict)
        }
    return lead_features
