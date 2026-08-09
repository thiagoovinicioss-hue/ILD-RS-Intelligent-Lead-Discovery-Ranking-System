"""Business endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from ildrs.storage.repositories import business_serialize, get_business, list_businesses

router = APIRouter(prefix="/api/v1/businesses", tags=["businesses"])


@router.get("")
async def list_businesses_route(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    source: str | None = None,
):
    ctx = request.app.state.context
    async with ctx.db.session() as session:
        rows = await list_businesses(session, limit=limit, offset=offset, source=source)
        return {"items": [business_serialize(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/{business_id}")
async def business_detail(business_id: str, request: Request):
    ctx = request.app.state.context
    async with ctx.db.session() as session:
        row = await get_business(session, business_id)
        if row is None:
            raise HTTPException(status_code=404, detail="business not found")
        return business_serialize(row)
