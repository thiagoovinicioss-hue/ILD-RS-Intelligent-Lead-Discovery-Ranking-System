"""Source factory — the only place that maps names to implementations."""

from __future__ import annotations

from ildrs.config import get_settings
from ildrs.sources.base import BusinessSource
from ildrs.sources.fixture import FixtureSource
from ildrs.sources.google_places import GooglePlacesSource


def create_source(name: str | None = None, *, api_key: str | None = None) -> BusinessSource:
    settings = get_settings()
    source_name = (name or settings.source).strip().lower()
    if source_name == "google_places":
        return GooglePlacesSource(api_key=api_key)
    if source_name == "fixture":
        return FixtureSource()
    raise ValueError(f"unknown source '{source_name}'")


__all__ = ["create_source", "BusinessSource"]
