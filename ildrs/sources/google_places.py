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

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from datetime import time as dtime

import httpx

from ildrs.config import get_settings
from ildrs.domain.entities import Business, Candidate
from ildrs.domain.provenance import DataSourceKind, ProvenanceMap, utcnow
from ildrs.normalization.normalizers import find_email, normalize_phone, normalize_website
from ildrs.sources.base import (
    DiscoveryQuery,
    NotConfiguredError,
    RateLimitedError,
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
    "places.currentOpeningHours",
    "nextPageToken",
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
        "currentOpeningHours",
    )
)


def _previous_weekday_time(now: datetime, weekday: int, t: dtime, offset_min: int) -> datetime:
    """Most recent occurrence of (weekday, time) at or before ``now``, as UTC-naive.

    Google ``day`` uses 0=Sunday..6=Saturday; schedule times are local and
    converted to UTC by subtracting ``utcOffsetMinutes``.
    """
    target_py = (weekday + 6) % 7  # -> Python weekday (Mon=0..Sun=6)
    days_back = (now.weekday() - target_py) % 7
    naive = (now - timedelta(days=days_back)).replace(
        hour=t.hour, minute=t.minute, second=0, microsecond=0
    )
    if naive > now:
        naive -= timedelta(days=7)
    if offset_min:
        naive = naive - timedelta(minutes=offset_min)
    return naive


def recent_activity_from_hours(place: dict, now: datetime | None = None) -> datetime | None:
    """Most recent open/close transition from ``currentOpeningHours``.

    Returns the latest open or close event at or before ``now`` (so a
    business that operated this week yields a concrete timestamp, while a
    permanently closed or schedule-less listing yields None).
    """
    hours = place.get("currentOpeningHours") or {}
    periods = hours.get("periods") or []
    if not periods:
        return None
    now = now or utcnow()
    offset = hours.get("utcOffsetMinutes") or 0
    latest: datetime | None = None
    for period in periods:
        for key in ("open", "close"):
            slot = period.get(key)
            if not slot:
                continue
            time_str = slot.get("time", "00:00")
            try:
                hh, mm = time_str.split(":")
                t = dtime(hour=int(hh), minute=int(mm))
            except (ValueError, AttributeError, TypeError):
                continue
            dt = _previous_weekday_time(now, slot.get("day", 0), t, offset)
            if latest is None or dt > latest:
                latest = dt
    return latest


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
        # in-memory response cache: key -> (expires_at, payload)
        self._cache: dict[str, tuple[float, dict]] = {}
        self._last_request_at = 0.0

    def check_configured(self) -> None:
        if not self._api_key:
            raise NotConfiguredError(
                "Google Places is not configured: set ILD_GOOGLE_PLACES_API_KEY "
                "(or choose ILD_SOURCE=fixture)."
            )

    async def discover(self, query: DiscoveryQuery) -> list[Candidate]:
        self.check_configured()
        query.validate()
        payload: dict = {
            "textQuery": query.query,
            "maxResultCount": max(1, min(query.page_size, 20)),
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
        if self._language:
            payload["languageCode"] = self._language

        places: list[dict] = []
        token: str | None = None
        while len(places) < query.limit:
            page_payload = dict(payload)
            page_payload["maxResultCount"] = max(
                1, min(query.page_size, 20, query.limit - len(places))
            )
            if token:
                page_payload["pageToken"] = token
            data = await self._request(
                "POST", "/places:searchText", payload=page_payload, fields=SEARCH_FIELDS
            )
            places.extend(data.get("places", []))
            token = data.get("nextPageToken")
            if not token:
                break
        return [self._candidate_from_place(p) for p in places[: query.limit]]

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
        settings = get_settings()
        cache_key = self._cache_key(method, path, field_mask, payload)
        if cached := self._cache_get(cache_key):
            return cached

        await self._throttle(settings.google_places_min_interval_ms)

        headers = {
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": field_mask,
            "Content-Type": "application/json",
        }
        url = f"{PLACES_API_BASE}{path}"
        timeout = httpx.Timeout(settings.google_places_timeout_seconds)
        backoff_s = settings.google_places_backoff_base_ms / 1000.0

        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            last_error: SourceError | None = None
            for attempt in range(settings.google_places_retries):
                try:
                    response = await client.request(method, url, json=payload, headers=headers)
                except httpx.HTTPError as exc:
                    last_error = SourceError(f"google places request failed: {exc}")
                    if attempt == settings.google_places_retries - 1:
                        break
                    await asyncio.sleep(backoff_s * (2**attempt))
                    continue

                self._last_request_at = time.monotonic()

                if response.status_code == 403:
                    raise SourceError(
                        "google places returned 403 Forbidden — check ILD_GOOGLE_PLACES_API_KEY "
                        "and billing/API enablement."
                    )
                if response.status_code == 429:
                    if attempt == settings.google_places_retries - 1:
                        raise RateLimitedError("google places rate limited (HTTP 429)")
                    await asyncio.sleep(backoff_s * (2**attempt))
                    continue
                if response.status_code >= 500:
                    if attempt == settings.google_places_retries - 1:
                        raise SourceError(
                            f"google places returned HTTP {response.status_code}: "
                            f"{response.text[:300]}"
                        )
                    await asyncio.sleep(backoff_s * (2**attempt))
                    continue
                if response.status_code != 200:
                    raise SourceError(
                        f"google places returned HTTP {response.status_code}: {response.text[:300]}"
                    )

                data = response.json()
                self._cache_set(cache_key, data, settings.google_places_cache_ttl_seconds)
                return data

        assert last_error is not None
        raise last_error

    def _cache_key(self, method: str, path: str, field_mask: str, payload: dict | None) -> str:
        blob = json.dumps(
            {"m": method, "p": path, "f": field_mask, "b": payload}, sort_keys=True, default=str
        )
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> dict | None:
        hit = self._cache.get(key)
        if not hit:
            return None
        expires_at, data = hit
        if time.monotonic() > expires_at:
            self._cache.pop(key, None)
            return None
        return data

    def _cache_set(self, key: str, data: dict, ttl_seconds: int) -> None:
        self._cache[key] = (time.monotonic() + max(0, ttl_seconds), data)

    async def _throttle(self, min_interval_ms: int) -> None:
        elapsed = time.monotonic() - self._last_request_at
        needed = min_interval_ms / 1000.0 - elapsed
        if needed > 0:
            await asyncio.sleep(needed)

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

        phone = normalize_phone(
            place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber")
        )
        provenance.set(
            "phone", DataSourceKind.DIRECT if phone else DataSourceKind.UNAVAILABLE, cls.name, phone
        )

        website = normalize_website(place.get("websiteUri"))
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

        email = find_email(str(place))
        provenance.set(
            "email",
            DataSourceKind.DIRECT if email else DataSourceKind.UNAVAILABLE,
            cls.name,
            email or None,
        )

        activity = recent_activity_from_hours(place)
        provenance.set(
            "recent_activity",
            DataSourceKind.DIRECT if activity else DataSourceKind.UNAVAILABLE,
            cls.name,
            activity.isoformat() if activity else None,
        )

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
            recent_activity=activity,
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
