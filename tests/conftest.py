"""Test configuration and shared fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from ildrs.domain.entities import Business, FeatureValue, FeatureVector
from ildrs.domain.provenance import DataSourceKind, ProvenanceMap
from ildrs.storage.database import Database


@pytest_asyncio.fixture
async def db(tmp_path) -> AsyncIterator[Database]:
    """Isolated SQLite database per test."""
    database = Database(url=f"sqlite+aiosqlite:///{tmp_path}/test.db")
    database.connect()
    from ildrs.storage.bootstrap import init

    await init(database)
    yield database
    await database.close()


def make_business(
    *,
    name: str = "Test Plumbing Co",
    category: str = "plumber",
    rating: float | None = 4.5,
    reviews: int = 100,
    has_website: bool = True,
    has_phone: bool = True,
    email: str = "",
    status: str = "OPERATIONAL",
    lat: float | None = None,
    lng: float | None = None,
) -> Business:
    provenance = ProvenanceMap()
    provenance.set("name", DataSourceKind.DIRECT, "fixture", name)
    provenance.set(
        "website",
        DataSourceKind.DIRECT if has_website else DataSourceKind.UNAVAILABLE,
        "fixture",
        "x.com" if has_website else None,
    )
    provenance.set(
        "rating",
        DataSourceKind.DIRECT if rating is not None else DataSourceKind.UNAVAILABLE,
        "fixture",
        rating,
    )
    provenance.set(
        "review_count",
        DataSourceKind.DIRECT if reviews else DataSourceKind.UNAVAILABLE,
        "fixture",
        reviews,
    )
    provenance.set(
        "business_status",
        DataSourceKind.DIRECT if status else DataSourceKind.UNAVAILABLE,
        "fixture",
        status,
    )
    provenance.set(
        "phone",
        DataSourceKind.DIRECT if has_phone else DataSourceKind.UNAVAILABLE,
        "fixture",
        "+1 (555) 000-0000" if has_phone else None,
    )
    provenance.set(
        "email",
        DataSourceKind.DIRECT if email else DataSourceKind.UNAVAILABLE,
        "fixture",
        email or None,
    )

    return Business(
        source="fixture",
        external_id="fix-test",
        name=name,
        category=category,
        google_rating=rating,
        review_count=reviews,
        business_status=status,
        website="https://example.com" if has_website else "",
        phone="+1 (512) 555-0142" if has_phone else "",
        email=email,
        latitude=lat,
        longitude=lng,
        provenance=provenance,
    )


def make_vector(
    values: dict[str, float] | None = None,
    *,
    weights: dict[str, float] | None = None,
) -> FeatureVector:
    from ildrs.config import get_settings

    configured = get_settings().feature_weights
    values = values or {
        "web_presence": 1.0,
        "rating_score": 0.875,
        "review_volume": 0.667,
        "business_status": 1.0,
        "contact_availability": 0.667,
        "category_fit": 1.0,
        "location_fit": 0.5,
    }
    w = weights or configured
    features = {
        key: FeatureValue(
            key=key,
            value=float(value),
            weight=float(w.get(key, 0.0)),
            provenance_kind="direct",
            raw_value=None,
        )
        for key, value in values.items()
    }
    return FeatureVector(business_id="b1", features=features)


@pytest.fixture(autouse=True)
def _clean_signal_handlers():
    """CLI tests install signal handlers; restore defaults afterwards."""
    import contextlib
    import signal

    yield
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(Exception):
            signal.signal(sig, signal.SIG_DFL)
