"""Data-access layer.

Repositories isolate SQLAlchemy from the rest of the application. The rest of
the codebase works with domain entities from ``ildrs.domain``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ildrs.domain.entities import (
    Business,
    HistoricalOutcome,
    Job,
)
from ildrs.domain.provenance import ProvenanceMap, utcnow
from ildrs.storage.models import (
    BusinessRow,
    HistoricalOutcomeRow,
    JobRow,
    LeadRow,
    NotificationRow,
    OutreachMonitorRow,
    OutreachRow,
)

# --------------------------------------------------------------------------
# Business
# --------------------------------------------------------------------------


def business_to_domain(row: BusinessRow) -> Business:
    return Business(
        source=row.source,
        external_id=row.external_id,
        name=row.name,
        address=row.address,
        phone=row.phone,
        website=row.website,
        email=row.email,
        latitude=row.latitude,
        longitude=row.longitude,
        category=row.category,
        subcategories=list(row.subcategories or []),
        google_rating=row.google_rating,
        review_count=row.review_count,
        business_status=row.business_status,
        website_analysis=row.website_analysis,
        social_links=list(row.social_links or []),
        recent_activity=row.recent_activity,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_verified_at=row.last_verified_at,
        provenance=ProvenanceMap.from_dict(row.provenance),
    )


def business_serialize(row: BusinessRow) -> dict:
    return {
        "id": row.id,
        "external_id": row.external_id,
        "source": row.source,
        "name": row.name,
        "address": row.address,
        "phone": row.phone,
        "website": row.website,
        "email": row.email,
        "latitude": row.latitude,
        "longitude": row.longitude,
        "category": row.category,
        "subcategories": row.subcategories,
        "google_rating": row.google_rating,
        "review_count": row.review_count,
        "business_status": row.business_status,
        "collected": row.collected,
        "features": row.features,
        "provenance": row.provenance,
        "website_analysis": row.website_analysis,
        "social_links": row.social_links,
        "recent_activity": row.recent_activity.isoformat() if row.recent_activity else None,
        "is_duplicate": row.is_duplicate,
        "duplicate_of": row.duplicate_of,
        "deduped_at": row.deduped_at.isoformat() if row.deduped_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
    }


def business_from_domain(b: Business) -> BusinessRow:
    return BusinessRow(
        external_id=b.external_id,
        source=b.source,
        name=b.name,
        address=b.address,
        phone=b.phone,
        website=b.website,
        email=b.email,
        latitude=b.latitude,
        longitude=b.longitude,
        category=b.category,
        subcategories=b.subcategories,
        google_rating=b.google_rating,
        review_count=b.review_count,
        business_status=b.business_status,
        website_analysis=b.website_analysis,
        social_links=b.social_links,
        recent_activity=b.recent_activity,
        provenance=b.provenance.to_dict(),
        created_at=b.created_at,
        updated_at=b.updated_at,
        last_verified_at=b.last_verified_at,
    )


def update_business_row(row: BusinessRow, b: Business) -> None:
    row.external_id = b.external_id
    row.name = b.name
    row.address = b.address
    row.phone = b.phone
    row.website = b.website
    row.email = b.email
    row.latitude = b.latitude
    row.longitude = b.longitude
    row.category = b.category
    row.subcategories = b.subcategories
    row.google_rating = b.google_rating
    row.review_count = b.review_count
    row.business_status = b.business_status
    if b.website_analysis is not None:
        row.website_analysis = b.website_analysis
    if b.social_links:
        row.social_links = b.social_links
    if b.recent_activity is not None:
        row.recent_activity = b.recent_activity
    row.provenance = b.provenance.to_dict()
    row.updated_at = utcnow()


async def upsert_business(session: AsyncSession, b: Business) -> BusinessRow:
    """Insert a new business or update the existing one (by source+external_id)."""
    existing: BusinessRow | None = None
    if b.external_id:
        result = await session.execute(
            select(BusinessRow).where(
                BusinessRow.source == b.source, BusinessRow.external_id == b.external_id
            )
        )
        existing = result.scalar_one_or_none()

    if existing is not None:
        update_business_row(existing, b)
        await session.flush()
        return existing
    row = business_from_domain(b)
    row.id = _new_id()
    session.add(row)
    await session.flush()
    return row


async def get_business(session: AsyncSession, business_id: str) -> BusinessRow | None:
    return await session.get(BusinessRow, business_id)


async def find_duplicate_candidate(session: AsyncSession, b: Business) -> BusinessRow | None:
    """Return an existing business that matches ``b`` by phone/domain/name.

    Used during discovery to avoid inserting candidates that already exist
    under a different external_id. Conservative — only high-precision matches
    count (see ``ildrs.normalization.deduplicator``).
    """
    from ildrs.normalization.deduplicator import duplicate_pair
    from ildrs.normalization.normalizers import normalize_name, normalize_phone, website_domain

    phone = normalize_phone(b.phone)
    domain = website_domain(b.website)
    name = normalize_name(b.name)
    conditions = []
    if phone:
        conditions.append(BusinessRow.phone == phone)
    if domain:
        conditions.append(BusinessRow.website.like(f"%{domain}%"))
    if name:
        conditions.append(func.lower(BusinessRow.name) == name)
    if not conditions:
        return None
    result = await session.execute(select(BusinessRow).where(or_(*conditions)))
    for row in result.scalars().all():
        if duplicate_pair(b, row):
            return row
    return None


async def list_businesses(
    session: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
    source: str | None = None,
    stale_before: datetime | None = None,
) -> list[BusinessRow]:
    stmt = select(BusinessRow).order_by(BusinessRow.updated_at.desc())
    if source:
        stmt = stmt.where(BusinessRow.source == source)
    if stale_before is not None:
        stmt = stmt.where(
            (BusinessRow.last_verified_at.is_(None)) | (BusinessRow.last_verified_at < stale_before)
        )
    result = await session.execute(stmt.offset(offset).limit(limit))
    return list(result.scalars().all())


async def count_businesses(session: AsyncSession, source: str | None = None) -> int:
    stmt = select(BusinessRow.id)
    if source:
        stmt = stmt.where(BusinessRow.source == source)
    result = await session.execute(stmt)
    return len(result.scalars().all())


async def mark_verified(session: AsyncSession, business_id: str) -> None:
    await session.execute(
        update(BusinessRow)
        .where(BusinessRow.id == business_id)
        .values(last_verified_at=utcnow(), updated_at=utcnow())
    )


async def uncollected_businesses(session: AsyncSession, *, limit: int = 100) -> list[BusinessRow]:
    result = await session.execute(
        select(BusinessRow)
        .where(BusinessRow.collected.is_(False))
        .order_by(BusinessRow.created_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def set_business_collected(
    session: AsyncSession, business_id: str, features: dict | None = None
) -> None:
    values: dict = {"collected": True, "updated_at": utcnow(), "last_verified_at": utcnow()}
    if features is not None:
        values["features"] = features
    await session.execute(update(BusinessRow).where(BusinessRow.id == business_id).values(**values))


async def store_business_features(session: AsyncSession, business_id: str, features: dict) -> None:
    await session.execute(
        update(BusinessRow)
        .where(BusinessRow.id == business_id)
        .values(features=features, updated_at=utcnow())
    )


async def businesses_with_features(
    session: AsyncSession, *, limit: int = 2000
) -> list[BusinessRow]:
    result = await session.execute(
        select(BusinessRow)
        .where(BusinessRow.features.isnot(None))
        .where(BusinessRow.features != {})
        .order_by(BusinessRow.updated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def set_website_analysis(session: AsyncSession, business_id: str, analysis: dict) -> None:
    await session.execute(
        update(BusinessRow)
        .where(BusinessRow.id == business_id)
        .values(website_analysis=analysis, updated_at=utcnow())
    )


async def clear_duplicate_flags(session: AsyncSession) -> int:
    """Reset duplicate flags before a dedup pass. Returns rows touched."""
    result = await session.execute(
        update(BusinessRow)
        .where(BusinessRow.is_duplicate.is_(True))
        .values(is_duplicate=False, duplicate_of=None, deduped_at=None, updated_at=utcnow())
    )
    return result.rowcount or 0


async def mark_duplicates(session: AsyncSession, duplicates: dict[str, str]) -> int:
    """Flag duplicate business ids with their canonical id. Returns rows touched."""
    marked = 0
    for business_id, canonical_id in duplicates.items():
        result = await session.execute(
            update(BusinessRow)
            .where(BusinessRow.id == business_id)
            .values(
                is_duplicate=True,
                duplicate_of=canonical_id,
                deduped_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        marked += result.rowcount or 0
    return marked


async def count_collected(session: AsyncSession) -> int:
    result = await session.execute(select(BusinessRow.id).where(BusinessRow.collected.is_(True)))
    return len(result.scalars().all())


async def count_analyzed(session: AsyncSession) -> int:
    result = await session.execute(
        select(BusinessRow.id)
        .where(BusinessRow.features.isnot(None))
        .where(BusinessRow.features != {})
    )
    return len(result.scalars().all())


async def count_valid_feature_vectors(session: AsyncSession) -> int:
    """Count businesses whose stored features pass validation."""
    from ildrs.features.validator import FeatureValidator

    rows = await businesses_with_features(session, limit=100000)
    validator = FeatureValidator()
    valid = 0
    from ildrs.pipeline.stages import _vector_from_stored

    for row in rows:
        report = validator.validate(_vector_from_stored(row))
        if report.valid:
            valid += 1
    return valid


async def count_high_quality_leads(session: AsyncSession, threshold: float = 70.0) -> int:
    result = await session.execute(select(LeadRow.id).where(LeadRow.rating >= threshold))
    return len(result.scalars().all())


async def high_value_leads(
    session: AsyncSession, threshold: float = 70.0, *, limit: int = 20
) -> list[tuple[str, str, float]]:
    """Leads at or above the high-value rating threshold, with business name.

    Returns ``(lead_id, business_name, rating)`` tuples ordered by rating.
    Used to raise a "high-value lead detected" notification when the set grows.
    """
    stmt = (
        select(LeadRow.id, BusinessRow.name, LeadRow.rating)
        .join(BusinessRow, LeadRow.business_id == BusinessRow.id)
        .where(LeadRow.rating >= threshold)
        .order_by(LeadRow.rating.desc())
    )
    result = await session.execute(stmt.limit(limit))
    return [(row.id, row.name, row.rating) for row in result.all()]


async def last_verification_time(session: AsyncSession) -> str | None:
    result = await session.execute(
        select(BusinessRow.last_verified_at)
        .where(BusinessRow.last_verified_at.isnot(None))
        .order_by(BusinessRow.last_verified_at.desc())
        .limit(1)
    )
    value = result.scalar_one_or_none()
    return value.isoformat() if value else None


async def last_stage_finished_at(session: AsyncSession, stage: str) -> str | None:
    result = await session.execute(
        select(JobRow.finished_at)
        .where(JobRow.stage == stage, JobRow.status == "completed")
        .order_by(JobRow.finished_at.desc())
        .limit(1)
    )
    value = result.scalar_one_or_none()
    return value.isoformat() if value else None


# --------------------------------------------------------------------------
# Leads
# --------------------------------------------------------------------------


def lead_serialize(row: LeadRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "business_id": row.business_id,
        "business_name": row.business.name if row.business else None,
        "rating": row.rating,
        "confidence": row.confidence,
        "model": row.model,
        "model_version": row.model_version,
        "expected_value": row.expected_value,
        "rank": row.rank,
        "percentile": row.percentile,
        "features": row.features,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def upsert_lead(
    session: AsyncSession,
    *,
    business_id: str,
    rating: float,
    confidence: float,
    model: str,
    model_version: str,
    features: dict[str, Any],
    expected_value: dict | None = None,
) -> LeadRow:
    result = await session.execute(select(LeadRow).where(LeadRow.business_id == business_id))
    row = result.scalar_one_or_none()
    now = utcnow()
    if row is None:
        row = LeadRow(
            id=_new_id(),
            business_id=business_id,
            rating=rating,
            confidence=confidence,
            model=model,
            model_version=model_version,
            expected_value=expected_value,
            features=features,
            status="new",
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
    else:
        row.rating = rating
        row.confidence = confidence
        row.model = model
        row.model_version = model_version
        row.expected_value = expected_value
        row.features = features
        row.updated_at = now
        await session.flush()
    return row


async def get_lead(session: AsyncSession, lead_id: str) -> LeadRow | None:
    stmt = select(LeadRow).options(selectinload(LeadRow.business)).where(LeadRow.id == lead_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_leads(
    session: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
    status: str | None = None,
    sort: str = "rank",
) -> list[LeadRow]:
    order_cols = {
        "rank": LeadRow.rank.asc().nullslast(),
        "rating": LeadRow.rating.desc(),
        "created": LeadRow.created_at.desc(),
    }
    stmt = select(LeadRow).options(selectinload(LeadRow.business))
    if status:
        stmt = stmt.where(LeadRow.status == status)
    result = await session.execute(
        stmt.order_by(order_cols.get(sort, order_cols["rank"])).offset(offset).limit(limit)
    )
    return list(result.scalars().all())


async def count_leads(session: AsyncSession, status: str | None = None) -> int:
    stmt = select(LeadRow.id)
    if status:
        stmt = stmt.where(LeadRow.status == status)
    result = await session.execute(stmt)
    return len(result.scalars().all())


async def lead_by_business(session: AsyncSession, business_id: str) -> LeadRow | None:
    result = await session.execute(select(LeadRow).where(LeadRow.business_id == business_id))
    return result.scalar_one_or_none()


async def leads_without_outreach(session: AsyncSession, *, limit: int = 100) -> list[LeadRow]:
    """Rated leads that have no outreach record yet (for auto-prepare)."""
    stmt = (
        select(LeadRow)
        .options(selectinload(LeadRow.business))
        .outerjoin(OutreachRow, OutreachRow.lead_id == LeadRow.id)
        .where(OutreachRow.id.is_(None))
        .where(LeadRow.rating > 0)
        .order_by(LeadRow.rating.desc())
    )
    result = await session.execute(stmt.limit(limit))
    return list(result.scalars().all())


async def set_lead_status(session: AsyncSession, lead_id: str, status: str) -> LeadRow | None:
    row = await session.get(LeadRow, lead_id)
    if row is None:
        return None
    row.status = status
    row.updated_at = utcnow()
    await session.flush()
    return row


async def assign_ranks(session: AsyncSession, ranked: list[tuple[str, int, float]]) -> None:
    """Apply (lead_id, rank, percentile) tuples."""
    for lead_id, rank, percentile in ranked:
        await session.execute(
            update(LeadRow)
            .where(LeadRow.id == lead_id)
            .values(rank=rank, percentile=percentile, updated_at=utcnow())
        )


async def lead_counts_by_status(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(select(LeadRow.status, LeadRow.id))
    counts: dict[str, int] = {}
    for status, _ in result.all():
        counts[status] = counts.get(status, 0) + 1
    return counts


# --------------------------------------------------------------------------
# Outreach
# --------------------------------------------------------------------------


def outreach_serialize(row: OutreachRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "lead_id": row.lead_id,
        "channel": row.channel,
        "status": row.status,
        "note": row.note,
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "message": row.message,
        "reason": row.reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "review_status": row.review_status,
        "sent_status": row.sent_status,
        "response_status": row.response_status,
        "outcome": row.outcome,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "next_check_at": row.next_check_at.isoformat() if row.next_check_at else None,
    }


async def create_outreach(
    session: AsyncSession,
    *,
    lead_id: str,
    channel: str,
    status: str = "queued",
    note: str = "",
    message: str = "",
    reason: str = "",
    review_status: str = "pending",
    sent_status: str = "draft",
) -> OutreachRow | None:
    lead = await session.get(LeadRow, lead_id)
    if lead is None:
        return None
    now = utcnow()
    row = OutreachRow(
        id=_new_id(),
        lead_id=lead_id,
        channel=channel,
        status=status,
        note=note,
        occurred_at=now,
        message=message,
        reason=reason,
        created_at=now,
        review_status=review_status,
        sent_status=sent_status,
    )
    session.add(row)
    await session.flush()
    return row


async def get_outreach(session: AsyncSession, outreach_id: str) -> OutreachRow | None:
    return await session.get(OutreachRow, outreach_id)


async def update_outreach_status(
    session: AsyncSession, outreach_id: str, status: str
) -> OutreachRow | None:
    row = await session.get(OutreachRow, outreach_id)
    if row is None:
        return None
    row.status = status
    row.occurred_at = utcnow()
    await session.flush()
    return row


async def outreach_for_lead(session: AsyncSession, lead_id: str) -> list[OutreachRow]:
    result = await session.execute(
        select(OutreachRow)
        .where(OutreachRow.lead_id == lead_id)
        .order_by(OutreachRow.occurred_at.desc())
    )
    return list(result.scalars().all())


async def list_outreach(
    session: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
    review_status: str | None = None,
    sent_status: str | None = None,
) -> list[OutreachRow]:
    stmt = (
        select(OutreachRow)
        .options(selectinload(OutreachRow.lead).selectinload(LeadRow.business))
        .order_by(OutreachRow.created_at.desc())
    )
    if review_status:
        stmt = stmt.where(OutreachRow.review_status == review_status)
    if sent_status:
        stmt = stmt.where(OutreachRow.sent_status == sent_status)
    result = await session.execute(stmt.offset(offset).limit(limit))
    return list(result.scalars().all())


async def pending_review_items(session: AsyncSession, *, limit: int = 100) -> list[OutreachRow]:
    """Outreach drafts awaiting human review (the review queue)."""
    stmt = (
        select(OutreachRow)
        .options(selectinload(OutreachRow.lead).selectinload(LeadRow.business))
        .where(OutreachRow.review_status == "pending")
        .order_by(OutreachRow.created_at.asc())
    )
    result = await session.execute(stmt.limit(limit))
    return list(result.scalars().all())


async def set_outreach_review_status(
    session: AsyncSession, outreach_id: str, review_status: str
) -> OutreachRow | None:
    row = await session.get(OutreachRow, outreach_id)
    if row is None:
        return None
    row.review_status = review_status
    await session.flush()
    return row


async def edit_outreach(
    session: AsyncSession, outreach_id: str, *, message: str, reason: str
) -> OutreachRow | None:
    row = await session.get(OutreachRow, outreach_id)
    if row is None:
        return None
    row.message = message
    row.reason = reason
    row.review_status = "edited"
    row.sent_status = "queued"
    await session.flush()
    return row


async def mark_outreach_sent(session: AsyncSession, outreach_id: str) -> OutreachRow | None:
    row = await session.get(OutreachRow, outreach_id)
    if row is None:
        return None
    row.sent_status = "sent"
    row.status = "sent"
    row.sent_at = utcnow()
    await session.flush()
    return row


async def update_outreach_response(
    session: AsyncSession,
    outreach_id: str,
    *,
    response_status: str,
    outcome: str = "",
    next_check_at: datetime | None = None,
) -> OutreachRow | None:
    row = await session.get(OutreachRow, outreach_id)
    if row is None:
        return None
    row.response_status = response_status
    if outcome:
        row.outcome = outcome
    row.last_checked_at = utcnow()
    if next_check_at is not None:
        row.next_check_at = next_check_at
    await session.flush()
    return row


async def mark_outreach_checked(
    session: AsyncSession, outreach_id: str, *, next_check_at: datetime
) -> OutreachRow | None:
    row = await session.get(OutreachRow, outreach_id)
    if row is None:
        return None
    row.last_checked_at = utcnow()
    row.next_check_at = next_check_at
    await session.flush()
    return row


# --------------------------------------------------------------------------
# Outreach monitoring
# --------------------------------------------------------------------------


def monitor_serialize(row: OutreachMonitorRow) -> dict[str, Any]:
    return {
        "source": row.source,
        "configured": row.configured,
        "status": row.status,
        "detail": row.detail,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "next_check_at": row.next_check_at.isoformat() if row.next_check_at else None,
    }


async def upsert_monitor(
    session: AsyncSession,
    *,
    source: str,
    configured: bool,
    status: str = "unavailable",
    detail: str = "",
    last_checked_at: datetime | None = None,
    next_check_at: datetime | None = None,
) -> OutreachMonitorRow:
    row = await session.get(OutreachMonitorRow, source)
    if row is None:
        row = OutreachMonitorRow(source=source, configured=configured, status=status)
        session.add(row)
    row.configured = configured
    row.status = status
    if detail:
        row.detail = detail
    if last_checked_at is not None:
        row.last_checked_at = last_checked_at
    if next_check_at is not None:
        row.next_check_at = next_check_at
    await session.flush()
    return row


async def list_monitors(session: AsyncSession) -> list[OutreachMonitorRow]:
    result = await session.execute(select(OutreachMonitorRow).order_by(OutreachMonitorRow.source))
    return list(result.scalars().all())


# --------------------------------------------------------------------------
# Historical outcomes
# --------------------------------------------------------------------------


async def record_outcome(
    session: AsyncSession,
    *,
    business_id: str,
    lead_id: str,
    outcome: str,
    outcome_value: int,
    features: dict[str, Any],
) -> HistoricalOutcomeRow:
    row = HistoricalOutcomeRow(
        id=_new_id(),
        business_id=business_id,
        lead_id=lead_id,
        outcome=outcome,
        outcome_value=outcome_value,
        features=features,
        recorded_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def list_outcomes(
    session: AsyncSession,
    *,
    limit: int = 500,
    offset: int = 0,
) -> list[HistoricalOutcomeRow]:
    result = await session.execute(
        select(HistoricalOutcomeRow)
        .order_by(HistoricalOutcomeRow.recorded_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_outcomes(session: AsyncSession) -> int:
    result = await session.execute(select(HistoricalOutcomeRow.id))
    return len(result.scalars().all())


async def outcomes_for_lead(session: AsyncSession, lead_id: str) -> list[HistoricalOutcomeRow]:
    """Historical outcomes recorded against a specific lead (newest first)."""
    result = await session.execute(
        select(HistoricalOutcomeRow)
        .where(HistoricalOutcomeRow.lead_id == lead_id)
        .order_by(HistoricalOutcomeRow.recorded_at.desc())
    )
    return list(result.scalars().all())


def outcome_serialize(row: HistoricalOutcomeRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "business_id": row.business_id,
        "lead_id": row.lead_id,
        "outcome": row.outcome,
        "outcome_value": row.outcome_value,
        "features": row.features,
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
    }


def outcome_domain(row: HistoricalOutcomeRow) -> HistoricalOutcome:
    return HistoricalOutcome(
        id=row.id,
        business_id=row.business_id,
        lead_id=row.lead_id,
        outcome=row.outcome,
        outcome_value=row.outcome_value,
        features=row.features,
        recorded_at=row.recorded_at,
    )


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


def job_domain(row: JobRow) -> Job:
    return Job(
        id=row.id,
        stage=row.stage,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        error=row.error,
        counts=dict(row.counts or {}),
        meta=dict(row.meta or {}),
    )


def job_serialize(row: JobRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "stage": row.stage,
        "status": row.status,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "error": row.error,
        "counts": row.counts,
        "meta": row.meta,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def create_job(session: AsyncSession, stage: str) -> JobRow:
    row = JobRow(id=_new_id(), stage=stage, status="pending", created_at=utcnow())
    session.add(row)
    await session.flush()
    return row


async def get_job(session: AsyncSession, job_id: str) -> JobRow | None:
    return await session.get(JobRow, job_id)


async def list_jobs(session: AsyncSession, *, limit: int = 50, offset: int = 0) -> list[JobRow]:
    result = await session.execute(
        select(JobRow).order_by(JobRow.created_at.desc()).offset(offset).limit(limit)
    )
    return list(result.scalars().all())


async def set_job_running(session: AsyncSession, job_id: str) -> None:
    await session.execute(
        update(JobRow).where(JobRow.id == job_id).values(status="running", started_at=utcnow())
    )


async def finish_job(
    session: AsyncSession,
    *,
    job_id: str,
    status: str,
    counts: dict[str, int] | None = None,
    meta: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    await session.execute(
        update(JobRow)
        .where(JobRow.id == job_id)
        .values(
            status=status,
            finished_at=utcnow(),
            counts=counts or {},
            meta=meta or {},
            error=error,
        )
    )


async def active_jobs(session: AsyncSession) -> list[JobRow]:
    result = await session.execute(select(JobRow).where(JobRow.status.in_(("pending", "running"))))
    return list(result.scalars().all())


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------


def notification_serialize(row: NotificationRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "level": row.level,
        "title": row.title,
        "body": row.body,
        "read": row.read,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def add_notification(
    session: AsyncSession, *, level: str, title: str, body: str = ""
) -> NotificationRow:
    row = NotificationRow(
        id=_new_id(), level=level, title=title, body=body, read=False, created_at=utcnow()
    )
    session.add(row)
    await session.flush()
    return row


async def list_notifications(session: AsyncSession, *, limit: int = 50) -> list[NotificationRow]:
    result = await session.execute(
        select(NotificationRow).order_by(NotificationRow.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def mark_notifications_read(
    session: AsyncSession, notification_ids: list[str] | None = None
) -> None:
    stmt = update(NotificationRow).values(read=True)
    if notification_ids:
        stmt = stmt.where(NotificationRow.id.in_(notification_ids))
    await session.execute(stmt)


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())
