"""Source interfaces (Protocols).

External providers are replaceable behind these interfaces. The pipeline,
features, rating, and API layers only ever depend on these types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ildrs.domain.entities import Business, Candidate


@dataclass
class DiscoveryQuery:
    """Structured discovery request — never a single hardcoded string.

    Providers may use any combination of these dimensions. At least one of
    ``query`` or ``category``/``keywords`` must be present.
    """

    query: str = ""
    category: str = ""
    keywords: list[str] = field(default_factory=list)
    language: str = ""
    region: str = ""
    latitude: float | None = None
    longitude: float | None = None
    radius_m: int = 20000
    limit: int = 50
    page_size: int = 20

    @property
    def has_terms(self) -> bool:
        return bool(self.query or self.category or self.keywords)

    def validate(self) -> None:
        """Validate a discovery query before handing it to a provider."""
        if not self.has_terms:
            raise ValueError("discovery query requires at least one of query, category, keywords")
        if self.limit < 1:
            raise ValueError("limit must be >= 1")
        if self.page_size < 1:
            raise ValueError("page_size must be >= 1")
        if self.radius_m < 1:
            raise ValueError("radius_m must be >= 1")
        if self.latitude is not None and not -90.0 <= self.latitude <= 90.0:
            raise ValueError("latitude out of range")
        if self.longitude is not None and not -180.0 <= self.longitude <= 180.0:
            raise ValueError("longitude out of range")


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


class QuotaError(SourceError):
    """Raised when a provider refuses the request because a quota was exceeded."""


class RateLimitedError(SourceError):
    """Raised when a provider rate-limits the request and retries are exhausted."""
