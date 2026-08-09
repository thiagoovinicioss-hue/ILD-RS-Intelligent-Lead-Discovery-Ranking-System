"""System endpoints: health, status, metrics."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from ildrs.config import get_settings
from ildrs.domain.entities import STAGE_NAMES
from ildrs.observability.metrics import metrics
from ildrs.rating.registry import available_models, create_model
from ildrs.storage.bootstrap import database_counts
from ildrs.storage.repositories import (
    active_jobs,
    count_analyzed,
    count_businesses,
    count_collected,
    count_high_quality_leads,
    count_leads,
    count_outcomes,
    count_valid_feature_vectors,
    last_stage_finished_at,
    last_verification_time,
    lead_counts_by_status,
    list_jobs,
    list_notifications,
    list_outcomes,
    mark_notifications_read,
    notification_serialize,
)

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health")
async def health(request: Request):
    ctx = request.app.state.context
    ok = ctx.db.is_connected
    return {"status": "ok" if ok else "degraded", "database": "connected" if ok else "disconnected"}


@router.get("/system/status")
async def system_status(request: Request):
    ctx = request.app.state.context
    settings = get_settings()

    async with ctx.db.session() as session:
        businesses = await count_businesses(session)
        leads = await count_leads(session)
        outcomes = await count_outcomes(session)
        lead_statuses = await lead_counts_by_status(session)
        jobs = await active_jobs(session)
        notifications = await list_notifications(session, limit=20)
        history = await list_jobs(session, limit=10)
        outcome_rows = await list_outcomes(session, limit=1000)
        collected = await count_collected(session)
        analyzed = await count_analyzed(session)
        valid_vectors = await count_valid_feature_vectors(session)
        high_quality = await count_high_quality_leads(session)
        last_verified = await last_verification_time(session)
        last_discovery = await last_stage_finished_at(session, "discover")
        last_rank = await last_stage_finished_at(session, "rank")

    responded = sum(
        1 for o in outcome_rows if o.outcome in ("responded", "interested", "converted")
    )
    interested = sum(1 for o in outcome_rows if o.outcome == "interested")
    converted = sum(1 for o in outcome_rows if o.outcome == "converted")
    next_verify = _next_verify(request)
    model = _model_status(outcome_count=outcomes)

    ranked = sum(1 for s in lead_statuses.values())
    return {
        "system": {
            "status": "running" if ctx.scheduler.is_running else "idle",
            "source": settings.source,
            "google_places_enabled": bool(settings.google_places_api_key),
            "version": _app_version(),
        },
        "discovery": {
            "businesses_found": businesses,
            "businesses_collected": collected,
            "last_discovery": last_discovery,
        },
        "analysis": {
            "businesses_analyzed": analyzed,
            "valid_feature_vectors": valid_vectors,
        },
        "rating": {
            "leads_rated": leads,
            "model": model["name"],
            "model_version": model["version"],
            "model_status": model,
            "ev": {
                "ready": settings.ev_deal_value is not None and settings.ev_cost is not None,
                "prob_state": "estimated",
                "probability": settings.ev_prior_probability,
                "deal_value": settings.ev_deal_value,
                "cost": settings.ev_cost,
                "expected_value": (
                    round(
                        settings.ev_prior_probability * settings.ev_deal_value - settings.ev_cost,
                        4,
                    )
                    if settings.ev_deal_value is not None and settings.ev_cost is not None
                    else None
                ),
            },
        },
        "ranking": {
            "leads_ranked": ranked,
            "high_quality_leads": high_quality,
            "last_rank": last_rank,
        },
        "workflow": {
            "pending_reviews": lead_statuses.get("new", 0),
            "outreach_active": lead_statuses.get("outreach", 0),
            "responses": responded,
            "interested": interested,
            "conversions": converted,
            "historical_outcomes": outcomes,
        },
        "review_queue": {
            "pending": await ctx.review.count_pending(),
            "approved": await _count_outreach_status(request, "approved"),
            "rejected": await _count_outreach_status(request, "rejected"),
        },
        "monitoring": {
            "configured": settings.outreach_monitor_source != "none",
            "source": settings.outreach_monitor_source,
            "interval_minutes": settings.outreach_monitor_interval_minutes,
            "sources": await ctx.monitor.status(),
        },
        "verification": {
            "last_verification": last_verified,
            "next_scheduled": next_verify,
        },
        "jobs": {
            "active": [{"id": j.id, "stage": j.stage, "status": j.status} for j in jobs],
            "history": [
                {
                    "id": j.id,
                    "stage": j.stage,
                    "status": j.status,
                    "error": j.error,
                    "counts": j.counts,
                    "created_at": j.created_at.isoformat() if j.created_at else None,
                }
                for j in history
            ],
        },
        "notifications": [
            {
                "id": n.id,
                "level": n.level,
                "title": n.title,
                "body": n.body,
                "read": n.read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifications
        ],
        "stages": list(STAGE_NAMES),
        "models": available_models(),
    }


@router.get("/system/metrics")
async def system_metrics(request: Request):
    ctx = request.app.state.context
    counts = await database_counts(ctx.db)
    return {
        "counters": metrics.snapshot()["counters"],
        "gauges": metrics.snapshot()["gauges"],
        "database_counts": counts,
    }


@router.get("/notifications")
async def notifications_route(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
):
    ctx = request.app.state.context
    async with ctx.db.session() as session:
        rows = await list_notifications(session, limit=limit)
        return {"items": [notification_serialize(r) for r in rows], "limit": limit}


class NotificationReadUpdate(BaseModel):
    ids: list[str] | None = None


@router.post("/notifications/read")
async def notifications_read(payload: NotificationReadUpdate, request: Request):
    ctx = request.app.state.context
    async with ctx.db.session() as session:
        await mark_notifications_read(session, payload.ids)
        await session.commit()
    return {"marked": "ok"}


def _app_version() -> str:
    from ildrs import __version__

    return __version__


def _model_status(outcome_count: int = 0) -> dict:
    settings = get_settings()
    try:
        model = create_model(settings.rating_model)
        status = model.status()
        status["status"] = _model_readiness(status, outcome_count)
        status["configured"] = settings.rating_model
        return status
    except Exception:
        return {
            "name": settings.rating_model,
            "configured": settings.rating_model,
            "error": True,
            "status": "error",
        }


def _model_readiness(status: dict, outcome_count: int = 0) -> str:
    if status.get("error"):
        return "error"
    if status.get("implemented") is False:
        return "unavailable — not implemented"
    if status.get("requires_fit") or "min_samples" in status:
        needs = outcome_count
        minimum = status.get("min_samples", 0)
        if needs >= minimum:
            return "calibrated"
        return f"awaiting data — needs {needs}/{minimum} outcomes"
    return "operational"


def _next_verify(request: Request) -> str | None:
    value = getattr(request.app.state, "_next_verify", None)
    return value


async def _count_outreach_status(request: Request, review_status: str) -> int:
    from ildrs.storage.repositories import list_outreach

    ctx = request.app.state.context
    async with ctx.db.session() as session:
        rows = await list_outreach(session, limit=100000, review_status=review_status)
        return len(rows)
