"""Deterministic synthetic source for development and tests.

Generates clearly-labeled synthetic businesses so the full pipeline, CLI, API,
and dashboard can be exercised without external credentials. Data provenance
is marked as ``fixture`` so it can never be confused with real-world data.
"""

from __future__ import annotations

import hashlib

from ildrs.config import get_settings
from ildrs.domain.entities import Business, Candidate
from ildrs.domain.provenance import DataSourceKind, ProvenanceMap
from ildrs.sources.base import DiscoveryQuery

# (name, category, rating, reviews, has_website, has_phone, status)
_FIXTURES: list[tuple[str, str, float | None, int, bool, bool, str]] = [
    ("Apex Plumbing Co", "plumber", 4.8, 320, True, True, "OPERATIONAL"),
    ("Blue Ridge Contractors", "general_contractor", 4.2, 110, True, True, "OPERATIONAL"),
    ("Summit Roofing & Repair", "roofing_contractor", 4.9, 205, True, True, "OPERATIONAL"),
    ("Clearwater Electric", "electrician", 4.0, 87, False, True, "OPERATIONAL"),
    ("Harbor HVAC Services", "hvac_contractor", 3.9, 64, True, True, "OPERATIONAL"),
    ("Lakeside Landscaping", "landscape_designer", 4.6, 152, True, True, "OPERATIONAL"),
    ("Granite Kitchen Remodel", "kitchen_remodeler", 4.7, 44, True, False, "OPERATIONAL"),
    ("Metro Foundation Works", "foundation_contractor", 3.5, 18, False, True, "OPERATIONAL"),
    ("Riverview Glass & Glazing", "glass_installation_service", 4.3, 96, True, True, "OPERATIONAL"),
    ("Oakwood Painting Co", "painter", 3.8, 130, True, True, "CLOSED_PERMANENTLY"),
    ("Prairie Fence Builders", "fence_installation", 4.4, 71, False, False, "OPERATIONAL"),
    ("Canyon Carpet Cleaning", "carpet_cleaning_service", 4.1, 258, True, True, "OPERATIONAL"),
    ("Cedar Point Pest Control", "pest_control_service", 4.5, 189, True, True, "OPERATIONAL"),
    ("Willow Tree Care", "tree_care", 4.2, 33, False, True, "OPERATIONAL"),
    ("Iron Gate Security", "security_system_installation", 4.8, 141, True, True, "OPERATIONAL"),
    ("Silver Lining Gutters", "gutters_repair_service", 3.7, 26, True, False, "OPERATIONAL"),
    (
        "Peak Water Heaters",
        "water_heater_installation_service",
        4.4,
        212,
        True,
        True,
        "OPERATIONAL",
    ),
    ("Northstar Flooring", "flooring_contractor", 4.0, 99, True, True, "OPERATIONAL"),
    ("Ember Fireplace Services", "fireplace_repair_service", 4.6, 58, False, True, "OPERATIONAL"),
    ("Boulder Masonry Group", "masonry_contractor", 3.6, 22, False, False, "OPERATIONAL"),
]


class FixtureSource:
    """Deterministic, clearly-labeled synthetic provider."""

    name = "fixture"

    def __init__(self) -> None:
        self._settings = get_settings()

    def _fixtures_for(
        self, query: DiscoveryQuery
    ) -> list[tuple[str, str, float | None, int, bool, bool, str]]:
        rows = _FIXTURES
        query_terms = [t for t in query.query.lower().split() if t]
        if query_terms:
            matches = []
            for row in rows:
                if any(term in row[1] or term in row[0].lower() for term in query_terms):
                    matches.append(row)
            if matches:
                rows = matches
        limit = max(1, query.limit)
        return rows[:limit]

    async def discover(self, query: DiscoveryQuery) -> list[Candidate]:
        candidates: list[Candidate] = []
        for i, (name, category, rating, reviews, has_website, _has_phone, _status) in enumerate(
            self._fixtures_for(query)
        ):
            seed = f"{name}:{category}"
            digest = hashlib.sha256(seed.encode()).hexdigest()
            lat = 30.2672 + (int(digest[0:4], 16) % 1000) / 10000.0
            lng = -97.7431 + (int(digest[4:8], 16) % 1000) / 10000.0
            candidates.append(
                Candidate(
                    source=self.name,
                    external_id=f"fix-{digest[:16]}",
                    name=name,
                    address=f"{(i + 1) * 137} Example Blvd, Austin, TX",
                    latitude=round(lat, 6),
                    longitude=round(lng, 6),
                    category=category,
                    subcategories=[category],
                    raw={"rating": rating, "reviews": reviews, "website": has_website},
                )
            )
        return candidates

    async def collect_details(self, candidate: Candidate) -> Business:
        raw = candidate.raw or {}
        rating = raw.get("rating")
        reviews = raw.get("reviews", 0)
        has_website = raw.get("website", False)
        has_phone = raw.get("phone", True)
        status = raw.get("status", "OPERATIONAL")

        provenance = ProvenanceMap()
        provenance.set("name", DataSourceKind.DIRECT, self.name, candidate.name)
        provenance.set("address", DataSourceKind.DIRECT, self.name, candidate.address)
        phone = (
            f"+1 (512) 555-{1000 + (int(candidate.external_id[-4:], 16) % 9000)}"
            if has_phone
            else ""
        )
        provenance.set(
            "phone",
            DataSourceKind.DIRECT if has_phone else DataSourceKind.UNAVAILABLE,
            self.name,
            phone,
        )
        website = (
            f"https://{candidate.name.lower().replace(' ', '').replace('&', '')}.example.com"
            if has_website
            else ""
        )
        provenance.set(
            "website",
            DataSourceKind.DIRECT if has_website else DataSourceKind.UNAVAILABLE,
            self.name,
            website,
        )
        provenance.set("email", DataSourceKind.UNAVAILABLE, self.name, None)
        provenance.set(
            "rating",
            DataSourceKind.DIRECT if rating is not None else DataSourceKind.UNAVAILABLE,
            self.name,
            rating,
        )
        provenance.set("review_count", DataSourceKind.DIRECT, self.name, reviews)
        provenance.set("business_status", DataSourceKind.DIRECT, self.name, status)
        provenance.set(
            "location", DataSourceKind.DERIVED, self.name, (candidate.latitude, candidate.longitude)
        )

        return Business(
            source=self.name,
            external_id=candidate.external_id,
            name=candidate.name,
            address=candidate.address,
            phone=phone,
            website=website,
            email="",
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            category=candidate.category,
            subcategories=list(candidate.subcategories),
            google_rating=rating,
            review_count=reviews,
            business_status=status,
            provenance=provenance,
        )
