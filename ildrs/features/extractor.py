"""Feature extraction: Business → FeatureVector.

Each feature value is computed deterministically and tagged with its own
provenance kind. Values are clamped to [0, 1]. Missing provider data never
gets fabricated — it maps to ``unavailable`` with value 0.0.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from ildrs.config import get_settings
from ildrs.domain.entities import Business, FeatureValue, FeatureVector
from ildrs.domain.provenance import DataSourceKind
from ildrs.features.definitions import feature_definitions
from ildrs.normalization.normalizers import (
    clamp,
    has_domain,
    normalize_email,
    normalize_phone,
    normalize_website,
)

REVIEW_CAP = 1000


def _log_scale(count: int, cap: int = REVIEW_CAP) -> float:
    if count <= 0:
        return 0.0
    return min(1.0, math.log10(count + 1) / math.log10(cap + 1))


def _rating_scale(rating: float | None) -> float:
    if rating is None:
        return 0.0
    return clamp((rating - 1.0) / 4.0)


class FeatureExtractor:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.definitions = feature_definitions()
        self.weights = self.settings.feature_weights

    def extract(self, business: Business, business_id: str = "") -> FeatureVector:
        w = self.weights
        p = business.provenance

        # web_presence ------------------------------------------------------
        website = normalize_website(business.website)
        web_value = 1.0 if has_domain(website) else 0.0
        web_kind = (
            DataSourceKind.DIRECT if p.is_available("website") else DataSourceKind.UNAVAILABLE
        )

        # rating_score ------------------------------------------------------
        rating_value = _rating_scale(business.google_rating)
        rating_kind = (
            DataSourceKind.DIRECT
            if business.google_rating is not None
            else DataSourceKind.UNAVAILABLE
        )

        # review_volume -----------------------------------------------------
        review_value = _log_scale(business.review_count)
        review_kind = (
            DataSourceKind.DIRECT if business.review_count > 0 else DataSourceKind.UNAVAILABLE
        )

        # business_status ---------------------------------------------------
        status = (business.business_status or "").upper()
        status_value = 1.0 if status == "OPERATIONAL" else 0.2
        status_kind = DataSourceKind.DIRECT if status else DataSourceKind.UNAVAILABLE

        # contact_availability ----------------------------------------------
        phone = normalize_phone(business.phone)
        email = normalize_email(business.email)
        contact_known = [
            bool(phone),
            bool(has_domain(website)),
            bool(email),
        ]
        known = [c for c in contact_known if c is not None]
        contact_value = sum(known) / len(known) if known else 0.0
        contact_kind = DataSourceKind.DERIVED if known else DataSourceKind.UNAVAILABLE

        # category_fit ------------------------------------------------------
        category_value = self._category_fit(business)
        category_kind = DataSourceKind.DERIVED if business.category else DataSourceKind.UNAVAILABLE

        # location_fit ------------------------------------------------------
        location_value, location_kind = self._location_fit(business)

        # website_quality ---------------------------------------------------
        website_value, website_kind = self._website_quality(business, website)

        # business_completeness ---------------------------------------------
        completeness_value, completeness_kind = self._completeness(business, phone, email, website)

        # recent_activity ---------------------------------------------------
        activity_value, activity_kind = self._recent_activity(business)

        # social_presence / social_activity ---------------------------------
        social_presence, social_activity, social_presence_kind, social_activity_kind = self._social(
            business
        )

        features: dict[str, FeatureValue] = {
            "web_presence": FeatureValue(
                "web_presence", web_value, w["web_presence"], web_kind.value, business.website
            ),
            "rating_score": FeatureValue(
                "rating_score",
                rating_value,
                w["rating_score"],
                rating_kind.value,
                business.google_rating,
            ),
            "review_volume": FeatureValue(
                "review_volume",
                review_value,
                w["review_volume"],
                review_kind.value,
                business.review_count,
            ),
            "business_status": FeatureValue(
                "business_status",
                status_value,
                w["business_status"],
                status_kind.value,
                business.business_status,
            ),
            "contact_availability": FeatureValue(
                "contact_availability",
                contact_value,
                w["contact_availability"],
                contact_kind.value,
                contact_known,
            ),
            "category_fit": FeatureValue(
                "category_fit",
                category_value,
                w["category_fit"],
                category_kind.value,
                business.category,
            ),
            "location_fit": FeatureValue(
                "location_fit",
                location_value,
                w["location_fit"],
                location_kind.value,
                (business.latitude, business.longitude),
            ),
            "website_quality": FeatureValue(
                "website_quality",
                website_value,
                w["website_quality"],
                website_kind.value,
                business.website_analysis,
            ),
            "business_completeness": FeatureValue(
                "business_completeness",
                completeness_value,
                w["business_completeness"],
                completeness_kind.value,
                business.completeness_fields(),
            ),
            "recent_activity": FeatureValue(
                "recent_activity",
                activity_value,
                w["recent_activity"],
                activity_kind.value,
                business.recent_activity.isoformat() if business.recent_activity else None,
            ),
            "social_presence": FeatureValue(
                "social_presence",
                social_presence,
                w["social_presence"],
                social_presence_kind.value,
                business.social_links,
            ),
            "social_activity": FeatureValue(
                "social_activity",
                social_activity,
                w["social_activity"],
                social_activity_kind.value,
                business.social_links,
            ),
        }
        return FeatureVector(business_id=business_id, features=features)

    def _category_fit(self, business: Business) -> float:
        targets = self.settings.target_categories
        if not business.category:
            return 0.0
        category = business.category.lower()
        if category in targets:
            return 1.0
        for sub in business.subcategories:
            if sub.lower() in targets:
                return 1.0
        if any(token in category for token in targets):
            return 0.8
        return 0.3

    def _location_fit(self, business: Business) -> tuple[float, DataSourceKind]:
        center = self.settings.discovery_location_coords
        if center is None or business.latitude is None or business.longitude is None:
            return 0.0, DataSourceKind.UNAVAILABLE
        distance = haversine_km(center[0], center[1], business.latitude, business.longitude)
        radius_km = max(1.0, self.settings.discovery_radius_m / 1000.0)
        score = math.exp(-distance / radius_km)
        return clamp(score), DataSourceKind.DERIVED

    def _website_quality(self, business: Business, website: str) -> tuple[float, DataSourceKind]:
        analysis = business.website_analysis or {}
        if not has_domain(website):
            return 0.0, DataSourceKind.UNAVAILABLE
        if not analysis:
            return 0.0, DataSourceKind.UNAVAILABLE  # not yet analyzed
        fetched = bool(analysis.get("fetched"))
        error = analysis.get("error")
        if not fetched or error:
            return 0.0, DataSourceKind.UNAVAILABLE
        signals = [
            bool(analysis.get("title")),
            bool(analysis.get("meta_description")),
            int(analysis.get("word_count") or 0) > 200,
            analysis.get("has_ssl", False),
        ]
        score = sum(1.0 for s in signals if s) / max(1, len(signals))
        return clamp(score), DataSourceKind.DERIVED

    def _completeness(
        self, business: Business, phone: str, email: str, website: str
    ) -> tuple[float, DataSourceKind]:
        core = [
            business.name,
            business.address,
            business.category,
            has_domain(website),
            phone,
            email,
            str(business.google_rating or ""),
            business.business_status,
        ]
        filled = sum(1 for value in core if value)
        value = filled / len(core)
        kind = DataSourceKind.DERIVED if filled else DataSourceKind.UNAVAILABLE
        return clamp(value), kind

    def _recent_activity(self, business: Business) -> tuple[float, DataSourceKind]:
        activity = business.recent_activity
        if activity is None:
            return 0.0, DataSourceKind.UNAVAILABLE
        age_days = (datetime.now(UTC) - activity).total_seconds() / 86400.0
        value = clamp(1.0 - age_days / 30.0)  # fully fresh at 0 days, 0 at 30+
        return value, DataSourceKind.DIRECT

    def _social(self, business: Business) -> tuple[float, float, DataSourceKind, DataSourceKind]:
        links = [normalize_website(link) for link in (business.social_links or []) if link]
        links = [link for link in links if link]
        if not links:
            return 0.0, 0.0, DataSourceKind.UNAVAILABLE, DataSourceKind.UNAVAILABLE
        presence = 1.0
        analysis = business.website_analysis or {}
        latest = analysis.get("latest_post_at") or analysis.get("social_latest_at") or ""
        if latest:
            try:
                latest_date = datetime.fromisoformat(latest)
                if latest_date.tzinfo is None:
                    latest_date = latest_date.replace(tzinfo=UTC)
                age_days = (datetime.now(UTC) - latest_date).total_seconds() / 86400.0
                activity = clamp(1.0 - age_days / 90.0)
                return presence, activity, DataSourceKind.DERIVED, DataSourceKind.DERIVED
            except ValueError:
                pass
        # links exist but recency is unknown — activity is not observed
        return presence, 0.0, DataSourceKind.DERIVED, DataSourceKind.UNAVAILABLE


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in kilometers (Haversine formula)."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
