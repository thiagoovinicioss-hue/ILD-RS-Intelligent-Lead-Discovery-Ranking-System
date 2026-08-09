"""Job endpoints: history + stage triggering."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ildrs.domain.entities import STAGE_NAMES
from ildrs.storage.repositories import job_serialize, list_jobs

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


class JobRunRequest(BaseModel):
    stage: str = Field(..., description="One of " + ", ".join(STAGE_NAMES))
    mode: Literal["sync", "async"] = Field(
        "async",
        description="'sync' waits for completion; 'async' runs in the background",
    )


@router.get("")
async def jobs_route(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    ctx = request.app.state.context
    async with ctx.db.session() as session:
        rows = await list_jobs(session, limit=limit, offset=offset)
        return {"items": [job_serialize(r) for r in rows], "limit": limit, "offset": offset}


@router.post("/run")
async def run_job_route(payload: JobRunRequest, request: Request):
    ctx = request.app.state.context
    if payload.stage not in STAGE_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown stage '{payload.stage}'; use {list(STAGE_NAMES)}",
        )
    if payload.mode == "sync":
        result = await ctx.orchestrator.run_stage_guarded(payload.stage)
        if result.get("status") == "failed":
            raise HTTPException(
                status_code=500,
                detail=f"stage '{payload.stage}' failed: {result.get('error', '')}",
            )
        return {"accepted": False, "result": result}
    task = asyncio.create_task(
        ctx.orchestrator.run_stage_guarded(payload.stage),
        name=f"api:job:{payload.stage}",
    )
    ctx.background_tasks.add(task)
    return {"accepted": True, "stage": payload.stage, "mode": "async"}
