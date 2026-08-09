"""Outreach endpoints: create attempts and transition outcomes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1", tags=["outreach"])


class OutreachCreate(BaseModel):
    channel: Literal["email", "phone", "linkedin", "other"]
    note: str = Field("", max_length=2000)


class OutreachTransition(BaseModel):
    status: Literal[
        "queued", "sent", "no_response", "responded", "interested", "declined", "converted"
    ]
    note: str = Field("", max_length=2000)


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
