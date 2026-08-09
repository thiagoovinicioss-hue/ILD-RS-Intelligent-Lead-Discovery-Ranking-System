"""Tests for data sources: fixture and Google Places (offline)."""

from __future__ import annotations

import json

import httpx
import pytest

from ildrs.domain.provenance import DataSourceKind
from ildrs.sources.base import DiscoveryQuery, NotConfiguredError
from ildrs.sources.fixture import FixtureSource
from ildrs.sources.google_places import PLACES_API_BASE, GooglePlacesSource


@pytest.fixture
def source() -> FixtureSource:
    return FixtureSource()


class TestFixtureSource:
    async def test_discover_returns_candidates(self, source):
        candidates = await source.discover(DiscoveryQuery(query="plumber", limit=5))
        # the fixture source filters rows by the query terms
        assert len(candidates) == 1
        assert candidates[0].name == "Apex Plumbing Co"
        assert all(c.source == "fixture" for c in candidates)
        assert all(c.external_id.startswith("fix-") for c in candidates)
        assert all(c.name for c in candidates)

    async def test_discover_filters_by_query(self, source):
        candidates = await source.discover(DiscoveryQuery(query="electric", limit=50))
        assert candidates
        assert all("electric" in c.name.lower() or "electric" in c.category for c in candidates)

    async def test_discover_limit(self, source):
        candidates = await source.discover(DiscoveryQuery(query="", limit=3))
        assert len(candidates) == 3

    async def test_collect_details_provenance(self, source):
        candidates = await source.discover(DiscoveryQuery(query="plumber", limit=1))
        business = await source.collect_details(candidates[0])
        assert business.provenance.get("rating").kind is DataSourceKind.DIRECT
        assert business.provenance.get("email").kind is DataSourceKind.UNAVAILABLE
        assert business.google_rating is not None


# ---------------------------------------------------------------------------
# Google Places with a recorded httpx transport (offline, no API key needed)
# ---------------------------------------------------------------------------

SEARCH_RESPONSE = {
    "places": [
        {
            "id": "ChIJTEST0001",
            "displayName": {"text": "Austin Repair Co"},
            "formattedAddress": "500 Congress Ave, Austin, TX",
            "location": {"latitude": 30.2672, "longitude": -97.7431},
            "rating": 4.6,
            "userRatingCount": 212,
            "primaryType": "plumber",
            "types": ["plumber", "roofing_contractor"],
            "nationalPhoneNumber": "(512) 555-0142",
            "internationalPhoneNumber": "+1 512-555-0142",
            "websiteUri": "https://austinrepair.example.com",
            "businessStatus": "OPERATIONAL",
        }
    ]
}

DETAIL_RESPONSE = SEARCH_RESPONSE["places"][0]


class MockTransport(httpx.AsyncBaseTransport):
    """Deterministic recorded transport: asserts the request contract, returns canned data."""

    def __init__(self, responses: dict[tuple[str, str], dict]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, str(request.url).replace(PLACES_API_BASE, ""))
        if key in self.responses:
            return httpx.Response(200, json=self.responses[key])
        if request.method == "POST" and "searchText" in request.url.path:
            return httpx.Response(200, json=self.responses[("POST", "/places:searchText")])
        return httpx.Response(200, json=self.responses[("GET", "/places/{id}")])


def places_source() -> GooglePlacesSource:
    transport = MockTransport(
        {
            ("POST", "/places:searchText"): SEARCH_RESPONSE,
            ("GET", "/places/{id}"): DETAIL_RESPONSE,
        }
    )
    return GooglePlacesSource(api_key="test-key", transport=transport)


class TestGooglePlaces:
    async def test_requires_api_key(self):
        with pytest.raises(NotConfiguredError):
            GooglePlacesSource(api_key=None).check_configured()

    async def test_discover_sends_expected_request(self):
        transport = MockTransport(
            {
                ("POST", "/places:searchText"): SEARCH_RESPONSE,
                ("GET", "/places/{id}"): DETAIL_RESPONSE,
            }
        )
        src = GooglePlacesSource(api_key="test-key", transport=transport)
        candidates = await src.discover(
            DiscoveryQuery(
                query="plumber austin", latitude=30.26, longitude=-97.74, radius_m=15000, limit=5
            )
        )
        assert len(candidates) == 1
        assert candidates[0].name == "Austin Repair Co"
        assert candidates[0].external_id == "ChIJTEST0001"
        assert candidates[0].latitude == 30.2672

        request = transport.requests[0]
        assert request.url.path == "/v1/places:searchText"
        assert request.headers["X-Goog-Api-Key"] == "test-key"
        assert "places.id" in request.headers["X-Goog-FieldMask"]
        payload = json.loads(request.content)
        assert payload["textQuery"] == "plumber austin"
        assert payload["maxResultCount"] == 5
        assert payload["locationBias"]["circle"]["radius"] == 15000

    async def test_collect_details_contract(self):
        src = places_source()
        candidates = await src.discover(DiscoveryQuery(query="plumber", limit=5))
        business = await src.collect_details(candidates[0])
        assert business.source == "google_places"
        assert business.name == "Austin Repair Co"
        assert business.phone == "+15125550142"
        assert business.website == "https://austinrepair.example.com"
        assert business.google_rating == 4.6
        assert business.review_count == 212
        assert business.business_status == "OPERATIONAL"
        assert business.provenance.get("email").kind is DataSourceKind.UNAVAILABLE
        assert business.provenance.get("rating").kind is DataSourceKind.DIRECT

    async def test_non_200_raises_source_error(self):
        class BadTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(429, json={"error": {"message": "quota"}})

        src = GooglePlacesSource(api_key="k", transport=BadTransport())
        from ildrs.sources.base import SourceError

        with pytest.raises(SourceError):
            await src.discover(DiscoveryQuery(query="plumber", limit=5))

    async def test_retries_then_succeeds(self):
        import ildrs.config as config_module
        from ildrs.config import Settings

        class FlakyTransport(httpx.AsyncBaseTransport):
            attempts = 0

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                self.attempts += 1
                if self.attempts == 1:
                    return httpx.Response(500, text="boom", request=request)
                return httpx.Response(200, json=SEARCH_RESPONSE, request=request)

        transport = FlakyTransport()
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            config_module,
            "_settings",
            Settings(google_places_retries=3, google_places_backoff_base_ms=1),
        )
        try:
            src = GooglePlacesSource(api_key="k", transport=transport)
            candidates = await src.discover(DiscoveryQuery(query="plumber", limit=5))
        finally:
            monkeypatch.undo()
        assert len(candidates) == 1
        assert transport.attempts == 2

    async def test_paginates_to_reach_limit(self):
        class TwoPageTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                payload = json.loads(request.content) if request.content else {}
                page = {"places": [SEARCH_RESPONSE["places"][0]]}
                if not payload.get("pageToken"):
                    page["nextPageToken"] = "tok-2"
                return httpx.Response(200, json=page, request=request)

        src = GooglePlacesSource(api_key="k", transport=TwoPageTransport())
        candidates = await src.discover(DiscoveryQuery(query="plumber", limit=2))
        assert len(candidates) == 2

    async def test_caches_repeated_request(self):
        class CountingTransport(httpx.AsyncBaseTransport):
            hits = 0

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                self.hits += 1
                return httpx.Response(200, json=SEARCH_RESPONSE, request=request)

        transport = CountingTransport()
        src = GooglePlacesSource(api_key="k", transport=transport)
        await src.discover(DiscoveryQuery(query="plumber", limit=5))
        await src.discover(DiscoveryQuery(query="plumber", limit=5))
        assert transport.hits == 1

    async def test_recent_activity_parsed_from_opening_hours(self):
        from ildrs.sources.google_places import recent_activity_from_hours

        place = {
            "currentOpeningHours": {
                "utcOffsetMinutes": -300,
                "periods": [
                    {
                        "open": {"day": 1, "time": "08:00"},
                        "close": {"day": 1, "time": "17:00"},
                    }
                ],
            }
        }
        from datetime import UTC, datetime

        now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
        activity = recent_activity_from_hours(place, now=now)
        assert activity is not None
        # Mon 08:00 open / 17:00 close local = 13:00 / 22:00 UTC; latest event wins
        assert activity.day == 3
        assert activity.hour == 22
