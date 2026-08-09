"""Google Places (New API) source adapter.

Implements the ``BusinessSource`` protocol using the Google Places API v1
Text Search and Place Details endpoints.

Reference: https://developers.google.com/maps/documentation/places/web-service

- Text Search  -> ``POST /v1/places:searchText`` (discovery)
- Place Detail -> ``GET /v1/places/{place_id}``   (collection)

The adapter is a thin, replaceable transport: no other module depends on
Google-specific shapes. Requires ``ILD_GOOGLE_PLACES_API_KEY``; without it
the source refuses to run with ``NotConfiguredError``.
"""

from __future__ import annotations

import logging

import httpx

from ildrs.config import get_settings
from ildrs.domain.entities import Business, Candidate
from ildrs.domain.provenance import DataSourceKind, ProvenanceMap
from ildrs.sources.base import (
    DiscoveryQuery,
    NotConfiguredError,
    SourceError,
)

logger = logging.getLogger("ildrs.sources.google_places")

PLACES_API_BASE = "https://places.googleapis.com/v1"

SEARCH_FIELDS = (
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.rating",
    "places.userRatingCount",
    "places.primaryType",
    "places.types",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.websiteUri",
    "places.businessStatus",
)

DETAIL_FIELDS = ",".join(
    (
        "id",
        "displayName",
        "formattedAddress",
        "location",
        "rating",
        "userRatingCount",
        "primaryType",
        "types",
        "nationalPhoneNumber",
        "internationalPhoneNumber",
        "websiteUri",
        "businessStatus",
        "googleMapsUri",
    )
)


class GooglePlacesSource:
    """Provider adapter for Google Places (New API)."""

    name = "google_places"

    def __init__(
        self, api_key: str | None = None, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        settings = get_settings()
        self._api_key = api_key if api_key is not None else settings.google_places_api_key
        self._region = settings.google_places_region
        self._language = settings.google_places_language
        # transport injection enables offline, deterministic tests
        self._transport = transport

    def check_configured(self) -> None:
        if not self._api_key:
            raise NotConfiguredError(
                "Google Places is not configured: set ILD_GOOGLE_PLACES_API_KEY "
                "(or choose ILD_SOURCE=fixture)."
            )

    async def discover(self, query: DiscoveryQuery) -> list[Candidate]:
        self.check_configured()
        payload: dict = {
            "textQuery": query.query,
            "maxResultCount": max(1, min(query.limit, 20)),
        }
        location_bias: dict | None = None
        if query.latitude is not None and query.longitude is not None:
            location_bias = {
                "circle": {
                    "center": {"latitude": query.latitude, "longitude": query.longitude},
                    "radius": query.radius_m,
                }
            }
        if location_bias:
            payload["locationBias"] = location_bias
        if self._region:
            payload["regionCode"] = self._region

        data = await self._request(
            "POST", "/places:searchText", payload=payload, fields=SEARCH_FIELDS
        )
        places = data.get("places", [])
        return [self._candidate_from_place(p) for p in places]

    async def collect_details(self, candidate: Candidate) -> Business:
        self.check_configured()
        if not candidate.external_id:
            raise SourceError("candidate has no external_id; cannot collect details")
        data = await self._request("GET", f"/places/{candidate.external_id}", fields=DETAIL_FIELDS)
        return self._business_from_place(data)

    # -- internal ----------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        fields: str | tuple[str, ...],
    ) -> dict:
        field_mask = ",".join(fields) if isinstance(fields, (tuple, list)) else fields
        headers = {
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": field_mask,
            "Content-Type": "application/json",
        }
        url = f"{PLACES_API_BASE}{path}"
        timeout = httpx.Timeout(30.0)
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            try:
                response = await client.request(method, url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise SourceError(f"google places request failed: {exc}") from exc

        if response.status_code == 403:
            raise SourceError(
                "google places returned 403 Forbidden — check ILD_GOOGLE_PLACES_API_KEY "
                "and billing/API enablement."
            )
        if response.status_code != 200:
            raise SourceError(
                f"google places returned HTTP {response.status_code}: {response.text[:300]}"
            )
        return response.json()

    @staticmethod
    def _display_name(place: dict) -> str:
        name = place.get("displayName", {})
        if isinstance(name, dict):
            return name.get("text", "")
        return str(name or "")

    @classmethod
    def _candidate_from_place(cls, place: dict) -> Candidate:
        location = place.get("location") or {}
        return Candidate(
            source=cls.name,
            external_id=place.get("id", ""),
            name=cls._display_name(place),
            address=place.get("formattedAddress", ""),
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
            category=place.get("primaryType", ""),
            subcategories=list(place.get("types", []) or []),
            raw=place,
        )

    @classmethod
    def _business_from_place(cls, place: dict) -> Business:
        location = place.get("location") or {}
        provenance = ProvenanceMap()
        name = cls._display_name(place)
        provenance.set("name", DataSourceKind.DIRECT, cls.name, name)

        address = place.get("formattedAddress", "")
        provenance.set(
            "address",
            DataSourceKind.DIRECT if address else DataSourceKind.UNAVAILABLE,
            cls.name,
            address,
        )

        phone = place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber", "")
        provenance.set(
            "phone", DataSourceKind.DIRECT if phone else DataSourceKind.UNAVAILABLE, cls.name, phone
        )

        website = place.get("websiteUri", "")
        provenance.set(
            "website",
            DataSourceKind.DIRECT if website else DataSourceKind.UNAVAILABLE,
            cls.name,
            website,
        )

        rating = place.get("rating")
        provenance.set(
            "rating",
            DataSourceKind.DIRECT if rating is not None else DataSourceKind.UNAVAILABLE,
            cls.name,
            rating,
        )

        review_count = place.get("userRatingCount") or 0
        provenance.set(
            "review_count",
            DataSourceKind.DIRECT
            if place.get("userRatingCount") is not None
            else DataSourceKind.UNAVAILABLE,
            cls.name,
            review_count,
        )

        status = place.get("businessStatus", "")
        provenance.set(
            "business_status",
            DataSourceKind.DIRECT if status else DataSourceKind.UNAVAILABLE,
            cls.name,
            status,
        )

        email = ""
        provenance.set(
            "email", DataSourceKind.UNAVAILABLE, cls.name, None
        )  # not provided by Google Places

        business = Business(
            source=cls.name,
            external_id=place.get("id"),
            name=name,
            address=address,
            phone=phone,
            website=website,
            email=email,
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
            category=place.get("primaryType", ""),
            subcategories=list(place.get("types", []) or []),
            google_rating=rating,
            review_count=review_count,
            business_status=status,
        )
        business.provenance = provenance

        lat, lng = business.latitude, business.longitude
        provenance.set(
            "location",
            DataSourceKind.DIRECT if lat is not None else DataSourceKind.UNAVAILABLE,
            cls.name,
            (lat, lng),
        )
        return business
