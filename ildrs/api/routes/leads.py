"""Lead endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ildrs.storage.repositories import (
    get_lead,
    lead_serialize,
    list_leads,
    outcome_serialize,
    outcomes_for_lead,
    outreach_for_lead,
    outreach_serialize,
    set_lead_status,
)

router = APIRouter(prefix="/api/v1/leads", tags=["leads"])


class LeadStatusUpdate(BaseModel):
    status: Literal["new", "reviewed", "outreach", "contacted", "won", "lost", "dismissed"]


@router.get("")
async def leads_route(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    sort: Literal["rank", "rating", "created"] = "rank",
):
    ctx = request.app.state.context
    async with ctx.db.session() as session:
        rows = await list_leads(session, limit=limit, offset=offset, status=status, sort=sort)
        items = []
        for row in rows:
            item = lead_serialize(row)
            item["business"] = business_serialize(row.business) if row.business else None
            items.append(item)
        return {"items": items, "limit": limit, "offset": offset, "total": len(items)}


@router.get("/{lead_id}")
async def lead_detail(lead_id: str, request: Request):
    ctx = request.app.state.context
    async with ctx.db.session() as session:
        row = await get_lead(session, lead_id)
        if row is None:
            raise HTTPException(status_code=404, detail="lead not found")
        item = lead_serialize(row)
        item["business"] = business_serialize(row.business) if row.business else None
        item["outreach"] = [outreach_serialize(o) for o in await outreach_for_lead(session, row.id)]
        item["outcomes"] = [outcome_serialize(o) for o in await outcomes_for_lead(session, row.id)]
        return item


@router.patch("/{lead_id}/status")
async def update_lead_status(lead_id: str, payload: LeadStatusUpdate, request: Request):
    ctx = request.app.state.context
    async with ctx.db.session() as session:
        row = await set_lead_status(session, lead_id, payload.status)
        await session.commit()
        if row is None:
            raise HTTPException(status_code=404, detail="lead not found")
        return {"id": row.id, "status": row.status}


def business_serialize(row) -> dict:
    from ildrs.storage.repositories import business_serialize as _ser

    return _ser(row)
