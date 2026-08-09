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
