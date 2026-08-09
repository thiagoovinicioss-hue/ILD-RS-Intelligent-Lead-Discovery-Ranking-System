"""Configuration endpoint (non-secret values only)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ildrs.config import get_settings

router = APIRouter(prefix="/api/v1/config", tags=["config"])


@router.get("")
async def config_route(request: Request):
    settings = get_settings()
    return settings.public_dict()
