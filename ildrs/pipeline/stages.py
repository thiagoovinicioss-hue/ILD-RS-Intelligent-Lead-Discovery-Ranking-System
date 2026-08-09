"""Pipeline stages.

Each stage is an independent, idempotent unit that can be run alone or in
sequence. Stages cooperate with cancellation: they check ``cancel.is_set()``
between units of work and raise ``JobCancelled``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC
from typing import Any

from ildrs.config import get_settings
from ildrs.domain.entities import Business, Candidate
from ildrs.features.extractor import FeatureExtractor
from ildrs.features.validator import FeatureValidator
from ildrs.notifications.notifier import Notifier
from ildrs.rating.base import ModelNotReadyError, RatingModel
from ildrs.rating.calibrated import UncalibratedFallback
from ildrs.rating.registry import create_model
from ildrs.sources.base import BusinessSource, DiscoveryQuery
from ildrs.storage.database import Database
from ildrs.storage.repositories import (
    businesses_with_features,
    find_duplicate_candidate,
    get_business,
    high_value_leads,
    list_businesses,
    set_business_collected,
    store_business_features,
    uncollected_businesses,
    upsert_business,
    upsert_lead,
)

logger = logging.getLogger("ildrs.pipeline.stages")


class JobCancelled(Exception):
    """Raised when a stage is interrupted by cancellation."""


async def check_cancel(cancel: asyncio.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise JobCancelled()


# --------------------------------------------------------------------------
# Discovery + collection
# --------------------------------------------------------------------------


async def discover_stage(
    db: Database,
    source: BusinessSource,
    notifier: Notifier,
    *,
    query: DiscoveryQuery | None = None,
    limit: int | None = None,
    dedupe: bool = True,
    cancel: asyncio.Event | None = None,
) -> dict[str, int]:
    settings = get_settings()
    if query is None:
        lat, lng = settings.discovery_location_coords or (None, None)
        query = DiscoveryQuery(
            query=settings.discovery_query,
            latitude=lat,
            longitude=lng,
            radius_m=settings.discovery_radius_m,
            limit=limit or settings.discovery_limit,
        )
    await check_cancel(cancel)
    logger.info("discovering businesses with source '%s' (query=%r)", source.name, query.query)
    candidates = await source.discover(query)
    logger.info("source returned %d candidate(s)", len(candidates))

    discovered = 0
    duplicates_skipped = 0
    async with db.session() as session:
        for i, candidate in enumerate(candidates):
            await check_cancel(cancel)
            business = Business(
                source=candidate.source,
                external_id=candidate.external_id,
                name=candidate.name,
                address=candidate.address,
                latitude=candidate.latitude,
                longitude=candidate.longitude,
                category=candidate.category,
                subcategories=list(candidate.subcategories),
            )
            if dedupe:
                existing = await find_duplicate_candidate(session, business)
                if existing is not None:
                    duplicates_skipped += 1
                    continue
            await upsert_business(session, business)
            discovered += 1
            if (i + 1) % 25 == 0:
                await session.commit()
        await session.commit()
    await notifier.send(
        "info",
        "Discovery complete",
        f"{discovered} business(es) found via {source.name} "
        f"({duplicates_skipped} duplicate(s) skipped).",
    )
    return {"discovered": discovered, "duplicates_skipped": duplicates_skipped}


async def collect_stage(
    db: Database,
    source: BusinessSource,
    notifier: Notifier,
    *,
    limit: int = 100,
    cancel: asyncio.Event | None = None,
) -> dict[str, int]:
    async with db.session() as session:
        rows = await uncollected_businesses(session, limit=limit)
        ids = [r.id for r in rows]
    logger.info("collecting details for %d business(es)", len(ids))

    collected = 0
    errors = 0
    analyzed_websites = 0
    for business_id in ids:
        await check_cancel(cancel)
        try:
            async with db.session() as session:
                row = await get_business(session, business_id)
                if row is None:
                    continue
                candidate = Candidate(
                    source=row.source,
                    external_id=row.external_id,
                    name=row.name,
                    address=row.address,
                    latitude=row.latitude,
                    longitude=row.longitude,
                    category=row.category,
                    subcategories=list(row.subcategories or []),
                )
            business = await source.collect_details(candidate)
            await _enrich_website(business)
            if business.website_analysis is not None:
                analyzed_websites += 1
            async with db.session() as session:
                await upsert_business(session, business)
                await set_business_collected(session, business_id)
                await session.commit()
            collected += 1
        except Exception as exc:
            errors += 1
            logger.warning("failed to collect details for business %s: %s", business_id, exc)

    await notifier.send(
        "info" if errors == 0 else "warning",
        "Collection complete",
        f"Collected details for {collected} business(es); {errors} error(s); "
        f"analyzed {analyzed_websites} website(s).",
    )
    return {"collected": collected, "errors": errors, "websites_analyzed": analyzed_websites}


async def _enrich_website(business: Business) -> None:
    """Optionally analyze the business website (conservative, opt-in)."""
    settings = get_settings()
    if not settings.enable_website_analysis:
        return
    if not business.website:
        return
    from dataclasses import asdict

    from ildrs.analysis.website import analyze_website

    analysis = await analyze_website(business.website)
    business.website_analysis = asdict(analysis)
    business.social_links = analysis.social_links_compact()


# --------------------------------------------------------------------------
# Analysis (feature extraction + validation)
# --------------------------------------------------------------------------


async def analyze_stage(
    db: Database,
    notifier: Notifier,
    *,
    limit: int = 2000,
    cancel: asyncio.Event | None = None,
) -> dict[str, int]:
    extractor = FeatureExtractor()
    validator = FeatureValidator()

    async with db.session() as session:
        rows = await list_businesses(session, limit=limit)
    logger.info("analyzing %d business(es)", len(rows))

    analyzed = 0
    valid = 0
    invalid = 0
    for row in rows:
        await check_cancel(cancel)
        business = _business_from_row(row)
        vector = extractor.extract(business, business_id=row.id)
        report = validator.validate(vector)
        async with db.session() as session:
            await store_business_features(session, row.id, vector.to_dict())
            await session.commit()
        analyzed += 1
        if report.valid:
            valid += 1
        else:
            invalid += 1
            logger.warning("business %s failed feature validation: %s", row.id, report.errors)

    await notifier.send(
        "info",
        "Analysis complete",
        f"Extracted and validated features for {analyzed} business(es) "
        f"({valid} valid, {invalid} invalid).",
    )
    return {"analyzed": analyzed, "valid": valid, "invalid": invalid}


# --------------------------------------------------------------------------
# Rating
# --------------------------------------------------------------------------


async def rate_stage(
    db: Database,
    notifier: Notifier,
    *,
    limit: int = 2000,
    cancel: asyncio.Event | None = None,
) -> dict[str, int]:
    model = await _prepared_model(db)
    validator = FeatureValidator()
    logger.info("rating businesses with model '%s'", model.name)

    settings = get_settings()
    high_threshold = settings.outreach_high_value_rating
    async with db.session() as session:
        rows = await businesses_with_features(session, limit=limit)
        existing_high = {
            lead_id for lead_id, _, _ in await high_value_leads(session, threshold=high_threshold)
        }

    rated = 0
    newly_high: list[tuple[str, str, float]] = []
    for row in rows:
        await check_cancel(cancel)
        vector = _vector_from_stored(row)
        result = model.predict(vector)
        # Confidence is filled from data-availability when the model does not
        # provide its own estimate (V1 leaves it at 0 by design).
        confidence = result.confidence or validator.validate(vector).availability
        expected_value = result.metadata.get("expected_value") if result.metadata else None
        async with db.session() as session:
            await upsert_lead(
                session,
                business_id=row.id,
                rating=result.rating,
                confidence=confidence,
                model=result.model,
                model_version=result.model_version,
                features=result.to_dict(),
                expected_value=expected_value,
            )
            await session.commit()
        rated += 1
        if result.rating >= high_threshold and row.id not in existing_high:
            newly_high.append((row.id, row.name, result.rating))

    if newly_high:
        names = ", ".join(name for _, name, _ in newly_high[:5])
        await notifier.high_value_lead(
            business_name=names or f"{len(newly_high)} lead(s)",
            rating=max(rating for _, _, rating in newly_high),
        )

    await notifier.send(
        "info",
        "Rating complete",
        f"Rated {rated} business(es) with model '{model.name}' ({model.version}).",
    )
    return {"rated": rated, "model": model.name, "model_version": model.version}


async def _prepared_model(db: Database) -> RatingModel:
    """Configured model, fitted automatically when historical data allows."""
    settings = get_settings()
    model = create_model(settings.rating_model)

    if not model.requires_fit():
        return model

    samples = await _load_samples(db)
    try:
        report = model.fit(samples)
        logger.info("model '%s' calibrated: %s", model.name, report.message)
        return model
    except ModelNotReadyError:
        fallback = UncalibratedFallback(target_name=settings.rating_model)
        logger.warning(
            "model '%s' is not calibrated (need >= %d outcomes); using V1 weights.",
            settings.rating_model,
            settings.rating_min_samples,
        )
        return fallback


async def _load_samples(db: Database) -> list[Any]:
    from ildrs.outreach.workflow import OutreachWorkflow
    from ildrs.rating.base import OutcomeSample

    workflow = OutreachWorkflow(db)
    raw = await workflow.outcome_samples()
    return [OutcomeSample(features=s["features"], outcome_value=s["outcome_value"]) for s in raw]


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------


async def rank_stage(
    db: Database,
    notifier: Notifier,
    *,
    cancel: asyncio.Event | None = None,
) -> dict[str, int]:
    from ildrs.ranking.engine import RankingEngine
    from ildrs.storage.repositories import assign_ranks, list_leads

    engine = RankingEngine()
    async with db.session() as session:
        leads = await list_leads(session, limit=100000, sort="rating")
        ranked = engine.dense_ranks([_ranked_lead(lead) for lead in leads])
        await assign_ranks(session, ranked)
        await session.commit()

    await notifier.send("info", "Ranking complete", f"Ranked {len(ranked)} lead(s).")
    return {"ranked": len(ranked)}


def _ranked_lead(lead) -> Any:
    from ildrs.ranking.engine import RankedLead

    return RankedLead(
        lead_id=lead.id,
        rating=lead.rating,
        confidence=lead.confidence,
        created_at=lead.created_at.isoformat() if lead.created_at else "",
    )


# --------------------------------------------------------------------------
# Verification (re-collect stale businesses)
# --------------------------------------------------------------------------


async def verify_stage(
    db: Database,
    source: BusinessSource,
    notifier: Notifier,
    *,
    limit: int = 100,
    cancel: asyncio.Event | None = None,
) -> dict[str, int]:
    from datetime import timedelta

    from ildrs.storage.repositories import mark_verified

    settings = get_settings()
    stale_before = _now_utc() - timedelta(hours=settings.verify_interval_hours)

    async with db.session() as session:
        rows = await list_businesses(session, limit=limit, stale_before=stale_before)

    verified = 0
    errors = 0
    for row in rows:
        await check_cancel(cancel)
        try:
            candidate = Candidate(
                source=row.source,
                external_id=row.external_id,
                name=row.name,
                address=row.address,
                latitude=row.latitude,
                longitude=row.longitude,
                category=row.category,
                subcategories=list(row.subcategories or []),
            )
            business = await source.collect_details(candidate)
            async with db.session() as session:
                await upsert_business(session, business)
                await mark_verified(session, row.id)
                await session.commit()
            verified += 1
        except Exception as exc:
            errors += 1
            logger.warning("verification failed for business %s: %s", row.id, exc)

    await notifier.send(
        "info" if errors == 0 else "warning",
        "Verification complete",
        f"Re-verified {verified} business(es); {errors} error(s).",
    )
    if errors:
        await notifier.verification_failed(errors=errors)
    return {"verified": verified, "errors": errors}


def _now_utc() -> Any:
    from datetime import datetime

    return datetime.now(UTC)


def _business_from_row(row) -> Business:
    from ildrs.domain.provenance import ProvenanceMap

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


def _vector_from_stored(row):
    from ildrs.domain.entities import FeatureValue, FeatureVector

    stored = row.features or {}
    features: dict[str, FeatureValue] = {}
    for key, data in stored.items():
        if not isinstance(data, dict):
            continue
        features[key] = FeatureValue(
            key=key,
            value=float(data.get("value", 0.0)),
            weight=float(data.get("weight", 0.0)),
            provenance_kind=data.get("provenance", "unavailable"),
            raw_value=data.get("raw_value"),
        )
    return FeatureVector(business_id=row.id, features=features)
