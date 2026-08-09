"""Tests for feature extraction and validation (Architecture §4)."""

from __future__ import annotations

import math

import pytest

from ildrs.domain.provenance import DataSourceKind
from ildrs.features.definitions import FEATURE_DEFINITIONS, assert_keys_match_config
from ildrs.features.extractor import FeatureExtractor, haversine_km
from ildrs.features.validator import FeatureValidator
from tests.conftest import make_business, make_vector


def test_feature_keys_match_config():
    assert_keys_match_config()


def test_all_features_documented():
    expected = {
        "web_presence",
        "rating_score",
        "review_volume",
        "business_status",
        "contact_availability",
        "category_fit",
        "location_fit",
        "website_quality",
        "business_completeness",
        "recent_activity",
        "social_presence",
        "social_activity",
    }
    assert set(FEATURE_DEFINITIONS) == expected


def test_every_definition_has_provenance_field():
    assert all(d.provenance_field for d in FEATURE_DEFINITIONS.values())


def test_extract_rating_and_review_scale():
    extractor = FeatureExtractor()
    business = make_business(rating=4.5, reviews=999)
    vector = extractor.extract(business)
    assert vector.features["rating_score"].value == pytest.approx((4.5 - 1.0) / 4.0)
    assert vector.features["review_volume"].value == pytest.approx(
        math.log10(1000) / math.log10(1001)
    )


def test_extract_review_volume_saturates():
    extractor = FeatureExtractor()
    saturated = extractor.extract(make_business(reviews=10_000)).features["review_volume"]
    assert saturated.value == 1.0


def test_extract_missing_data_is_unavailable_not_fabricated():
    extractor = FeatureExtractor()
    business = make_business(rating=None, reviews=0, has_website=False, has_phone=False)
    vector = extractor.extract(business)
    assert vector.features["rating_score"].value == 0.0
    assert vector.features["rating_score"].provenance_kind == DataSourceKind.UNAVAILABLE.value
    assert vector.features["web_presence"].value == 0.0
    assert vector.features["review_volume"].value == 0.0


def test_extract_business_status_mapping():
    extractor = FeatureExtractor()
    operating = extractor.extract(make_business(status="OPERATIONAL"))
    closed = extractor.extract(make_business(status="CLOSED_PERMANENTLY"))
    assert operating.features["business_status"].value == 1.0
    assert closed.features["business_status"].value == 0.2


def test_extract_contact_availability_share():
    extractor = FeatureExtractor()
    full = extractor.extract(make_business(has_website=True, has_phone=True, email="a@b.co"))
    assert full.features["contact_availability"].value == pytest.approx(1.0)
    only_phone = extractor.extract(make_business(has_website=False, email=""))
    assert only_phone.features["contact_availability"].value == pytest.approx(1 / 3)


def test_extract_category_fit():
    extractor = FeatureExtractor()
    exact = extractor.extract(make_business(category="plumber"))
    assert exact.features["category_fit"].value == 1.0
    fuzzy = extractor.extract(make_business(category="contractor services"))
    assert fuzzy.features["category_fit"].value == pytest.approx(0.8)
    unrelated = extractor.extract(make_business(category="bakery"))
    assert unrelated.features["category_fit"].value == pytest.approx(0.3)


def test_extract_location_fit_near_center(monkeypatch):
    import ildrs.config as config_module
    from ildrs.config import Settings

    monkeypatch.setattr(
        config_module,
        "_settings",
        Settings(discovery_location="30.2672,-97.7431", discovery_radius_m=10000),
    )
    extractor = FeatureExtractor()
    near = extractor.extract(make_business(lat=30.2672, lng=-97.7431))
    assert near.features["location_fit"].value == pytest.approx(1.0)
    assert near.features["location_fit"].provenance_kind == DataSourceKind.DERIVED.value


def test_extract_location_unavailable_without_coords():
    extractor = FeatureExtractor()
    vector = extractor.extract(make_business(lat=None, lng=None))
    assert vector.features["location_fit"].value == 0.0
    assert vector.features["location_fit"].provenance_kind == DataSourceKind.UNAVAILABLE.value


def test_haversine_known_distance():
    # Austin → Dallas ≈ 293 km
    distance = haversine_km(30.2672, -97.7431, 32.7767, -96.7970)
    assert 285 < distance < 310


def test_extract_website_quality_requires_fetched_content():
    extractor = FeatureExtractor()
    plain = make_business(has_website=True)
    assert extractor.extract(plain).features["website_quality"].value == 0.0  # not analyzed

    good = make_business(has_website=True)
    good.website_analysis = {
        "fetched": True,
        "error": None,
        "title": "Apex Plumbing",
        "meta_description": "desc",
        "word_count": 400,
        "has_ssl": True,
    }
    assert extractor.extract(good).features["website_quality"].value == pytest.approx(1.0)

    broken = make_business(has_website=True)
    broken.website_analysis = {"fetched": False, "error": "timeout", "title": "", "word_count": 0}
    assert extractor.extract(broken).features["website_quality"].value == pytest.approx(0.1)


def test_extract_business_completeness():
    extractor = FeatureExtractor()
    full = make_business(email="a@b.co")
    full_completeness = extractor.extract(full).features["business_completeness"]
    assert full_completeness.value == pytest.approx(7 / 8)  # address is empty

    sparse = make_business(
        name="", category="", has_website=False, has_phone=False, rating=None, reviews=0, status=""
    )
    sparse_completeness = extractor.extract(sparse).features["business_completeness"]
    assert sparse_completeness.value == pytest.approx(0.0)
    assert sparse_completeness.provenance_kind == "unavailable"


def test_extract_recent_activity_decays():
    from datetime import UTC, datetime, timedelta

    extractor = FeatureExtractor()
    fresh = make_business()
    fresh.recent_activity = datetime.now(UTC) - timedelta(days=1)
    assert extractor.extract(fresh).features["recent_activity"].value == pytest.approx(
        1.0 - 1 / 30, abs=0.05
    )

    stale = make_business()
    stale.recent_activity = datetime.now(UTC) - timedelta(days=120)
    stale_value = extractor.extract(stale).features["recent_activity"].value
    assert stale_value == pytest.approx(0.0, abs=0.05)

    none = make_business()
    none.recent_activity = None
    assert extractor.extract(none).features["recent_activity"].value == 0.0
    assert extractor.extract(none).features["recent_activity"].provenance_kind == "unavailable"


def test_extract_social_presence_and_activity():
    extractor = FeatureExtractor()
    none = make_business()
    assert extractor.extract(none).features["social_presence"].value == 0.0

    active = make_business()
    active.social_links = ["https://www.facebook.com/apexplumbing"]
    assert extractor.extract(active).features["social_presence"].value == 1.0
    assert extractor.extract(active).features["social_activity"].value > 0.0

    dormant = make_business()
    dormant.social_links = ["https://www.facebook.com/apexplumbing"]
    dormant.website_analysis = {"latest_post_at": "2019-01-01"}
    assert extractor.extract(dormant).features["social_activity"].value == pytest.approx(
        0.0, abs=0.05
    )


def test_validator_reports_availability_and_validity():
    validator = FeatureValidator()
    vector = make_vector()
    report = validator.validate(vector)
    assert report.valid
    assert 0.0 <= report.availability <= 1.0


def test_validator_rejects_out_of_range():
    validator = FeatureValidator()
    vector = make_vector({"web_presence": 1.5})
    report = validator.validate(vector)
    assert not report.valid
    assert report.errors
