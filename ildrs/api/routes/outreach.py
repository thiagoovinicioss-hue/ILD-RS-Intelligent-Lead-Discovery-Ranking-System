"""Outreach management endpoints.

Review queue + response monitoring. Human review is mandatory before any
message is marked sendable; nothing here performs bulk or automated sending.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ildrs.storage.repositories import outreach_serialize

router = APIRouter(prefix="/api/v1", tags=["outreach"])


class OutreachCreate(BaseModel):
    channel: Literal["email", "phone", "linkedin", "other"]
    note: str = Field("", max_length=2000)


class OutreachTransition(BaseModel):
    status: Literal[
        "queued", "sent", "no_response", "responded", "interested", "declined", "converted"
    ]
    note: str = Field("", max_length=2000)


class PrepareRequest(BaseModel):
    channel: Literal["email", "phone", "linkedin", "other"] = "email"


class ReviewDecision(BaseModel):
    note: str = Field("", max_length=2000)


class ReviewEdit(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    reason: str = Field("", max_length=4000)


@router.get("/outreach")
async def list_outreach_route(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    review_status: str | None = Query(None),
    sent_status: str | None = Query(None),
):
    ctx = request.app.state.context
    async with ctx.db.session() as session:
        from ildrs.storage.repositories import list_outreach

        rows = await list_outreach(
            session,
            limit=limit,
            offset=offset,
            review_status=review_status,
            sent_status=sent_status,
        )
        return {
            "items": [outreach_serialize(r) for r in rows],
            "limit": limit,
            "offset": offset,
        }


@router.get("/outreach/pending")
async def review_queue_route(request: Request, limit: int = Query(100, ge=1, le=500)):
    ctx = request.app.state.context
    items = await ctx.review.list_pending(limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/outreach/monitoring")
async def monitoring_route(request: Request):
    ctx = request.app.state.context
    return {"sources": await ctx.monitor.status()}


@router.post("/outreach/monitoring/run")
async def monitoring_run_route(request: Request):
    ctx = request.app.state.context
    result = await ctx.monitor.run_once()
    return result


@router.post("/leads/{lead_id}/outreach/prepare")
async def prepare_outreach_route(lead_id: str, payload: PrepareRequest, request: Request):
    ctx = request.app.state.context
    result = await ctx.review.prepare(lead_id=lead_id, channel=payload.channel)
    if not result.ok:
        raise HTTPException(status_code=404, detail=result.error)
    return {
        "id": result.data["id"],
        "lead_id": lead_id,
        "review_status": result.data.get("review_status"),
        "duplicate": result.data.get("duplicate", False),
    }


@router.get("/outreach/{outreach_id}")
async def get_outreach_route(outreach_id: str, request: Request):
    ctx = request.app.state.context
    async with ctx.db.session() as session:
        from ildrs.storage.repositories import get_outreach

        row = await get_outreach(session, outreach_id)
        if row is None:
            raise HTTPException(status_code=404, detail="outreach not found")
        return outreach_serialize(row)


@router.post("/outreach/{outreach_id}/approve")
async def approve_outreach_route(outreach_id: str, payload: ReviewDecision, request: Request):
    ctx = request.app.state.context
    result = await ctx.review.approve(outreach_id=outreach_id)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error)
    return {"id": outreach_id, "review_status": result.data["review_status"]}


@router.post("/outreach/{outreach_id}/edit")
async def edit_outreach_route(outreach_id: str, payload: ReviewEdit, request: Request):
    ctx = request.app.state.context
    result = await ctx.review.edit(
        outreach_id=outreach_id, message=payload.message, reason=payload.reason
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error)
    return {"id": outreach_id, "review_status": result.data["review_status"]}


@router.post("/outreach/{outreach_id}/reject")
async def reject_outreach_route(outreach_id: str, payload: ReviewDecision, request: Request):
    ctx = request.app.state.context
    result = await ctx.review.reject(outreach_id=outreach_id, note=payload.note)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error)
    return {"id": outreach_id, "review_status": result.data["review_status"]}


@router.post("/outreach/{outreach_id}/send")
async def send_outreach_route(outreach_id: str, request: Request):
    ctx = request.app.state.context
    result = await ctx.review.mark_sent(outreach_id=outreach_id)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error)
    return {"id": outreach_id, "sent_status": result.data["sent_status"]}


@router.post("/leads/{lead_id}/outreach")
async def create_outreach_route(lead_id: str, payload: OutreachCreate, request: Request):
    ctx = request.app.state.context
    result = await ctx.outreach.open(lead_id=lead_id, channel=payload.channel, note=payload.note)
    if not result.ok:
        raise HTTPException(status_code=404, detail=result.error)
    return {
        "id": result.data["id"],
        "lead_id": lead_id,
        "channel": result.data["channel"],
        "status": "queued",
    }


@router.patch("/outreach/{outreach_id}")
async def transition_outreach_route(
    outreach_id: str, payload: OutreachTransition, request: Request
):
    ctx = request.app.state.context
    result = await ctx.outreach.transition(outreach_id=outreach_id, status=payload.status)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error)
    return {"id": outreach_id, "status": payload.status}
