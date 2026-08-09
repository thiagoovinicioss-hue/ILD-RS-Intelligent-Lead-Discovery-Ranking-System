"""Source interfaces (Protocols).

External providers are replaceable behind these interfaces. The pipeline,
features, rating, and API layers only ever depend on these types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ildrs.domain.entities import Business, Candidate


@dataclass
class DiscoveryQuery:
    query: str
    latitude: float | None = None
    longitude: float | None = None
    radius_m: int = 20000
    limit: int = 50


@runtime_checkable
class BusinessSource(Protocol):
    """Common interface every data provider must implement."""

    name: str

    async def discover(self, query: DiscoveryQuery) -> list[Candidate]:
        """Search for candidate businesses. Returns raw candidates."""
        ...

    async def collect_details(self, candidate: Candidate) -> Business:
        """Enrich a candidate into a full business with provenance."""
        ...


class SourceError(RuntimeError):
    """Raised when a source fails to provide data."""


class NotConfiguredError(SourceError):
    """Raised when the source cannot run because configuration is missing."""
