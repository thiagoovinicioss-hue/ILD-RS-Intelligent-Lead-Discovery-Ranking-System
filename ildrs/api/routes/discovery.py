"""Discovery and deduplication endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ildrs.normalization.deduplicator import summarize
from ildrs.sources.base import DiscoveryQuery
from ildrs.storage.repositories import (
    business_to_domain,
    clear_duplicate_flags,
    list_businesses,
    mark_duplicates,
)

router = APIRouter(prefix="/api/v1", tags=["discovery"])


class DiscoveryRequest(BaseModel):
    query: str = Field("", description="Free-text search query")
    category: str = Field("", description="Category token")
    keywords: list[str] = Field(default_factory=list, description="Additional keywords")
    language: str = Field("", description="Preferred language code")
    region: str = Field("", description="Region code (e.g. US)")
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    radius_m: int = Field(20000, ge=1, le=100000)
    limit: int = Field(50, ge=1, le=200)
    dedupe: bool = Field(True, description="Skip candidates matching existing businesses")
    persist: bool = Field(True, description="Store discovered businesses in the database")


@router.post("/discover")
async def run_discovery(payload: DiscoveryRequest, request: Request):
    ctx = request.app.state.context
    query = DiscoveryQuery(
        query=payload.query,
        category=payload.category,
        keywords=payload.keywords,
        language=payload.language,
        region=payload.region,
        latitude=payload.latitude,
        longitude=payload.longitude,
        radius_m=payload.radius_m,
        limit=payload.limit,
    )
    try:
        query.validate()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not payload.persist:
        candidates = await ctx.source.discover(query)
        return {
            "persisted": False,
            "found": len(candidates),
            "candidates": [_candidate_json(c) for c in candidates],
        }

    result = await ctx.orchestrator.run_stage_guarded(
        "discover", query=query, dedupe=payload.dedupe, cancel=asyncio.Event()
    )
    if result.get("status") == "failed":
        raise HTTPException(
            status_code=500,
            detail=f"discovery failed: {result.get('error', '')}",
        )
    return {"persisted": True, "result": result}


class DedupRequest(BaseModel):
    limit: int = Field(100000, ge=1, le=1000000)


@router.post("/dedup")
async def run_dedup(payload: DedupRequest, request: Request):
    ctx = request.app.state.context
    async with ctx.db.session() as session:
        rows = await list_businesses(session, limit=payload.limit)
        businesses = [business_to_domain(r) for r in rows]
        clusters, duplicate_count = summarize(businesses, (r.id for r in rows))
        mapping = {
            member_id: cluster.canonical_id
            for cluster in clusters
            for member_id in cluster.duplicate_ids
        }
        async with ctx.db.session() as session:
            cleared = await clear_duplicate_flags(session)
            marked = await mark_duplicates(session, mapping)
            await session.commit()
    return {
        "scanned": len(rows),
        "clusters": [
            {
                "cluster_id": c.cluster_id,
                "canonical_id": c.canonical_id,
                "duplicate_ids": c.duplicate_ids,
                "member_ids": c.member_ids,
            }
            for c in clusters
        ],
        "duplicates": duplicate_count,
        "flags_cleared": cleared,
        "flags_set": marked,
    }


def _candidate_json(candidate) -> dict:
    return {
        "source": candidate.source,
        "external_id": candidate.external_id,
        "name": candidate.name,
        "address": candidate.address,
        "latitude": candidate.latitude,
        "longitude": candidate.longitude,
        "category": candidate.category,
        "subcategories": list(candidate.subcategories),
    }
