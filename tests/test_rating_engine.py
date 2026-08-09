"""Tests for the rating engine mathematical core.

Covers normalization, nonlinear transforms, exponential time decay, weighted
rating, explanations, confidence (rating != confidence), expected value,
centralized config, determinism, and edge cases.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ildrs.config import get_settings
from ildrs.domain.entities import FeatureValue, FeatureVector
from ildrs.rating import (
    ExpectedValue,
    FutureMLModel,
    FutureProbabilisticModel,
    FutureStatisticalModel,
    RatingConfig,
    WeightedRatingModel,
    available_models,
    build_explanation,
    confidence_from_features,
    confidence_label,
    create_model,
    days_between,
    decay_rate_from_half_life,
    explain_feature,
    exponential_decay,
    format_contribution,
    half_life,
    normalize_binary,
    normalize_categorical,
    normalize_count_log,
    normalize_feature,
    normalize_passthrough,
    normalize_score01,
    normalize_weights,
    parse_timestamp,
    transform_feature,
    transform_identity,
    transform_quadratic,
)
from ildrs.rating.base import ModelNotImplemented
from ildrs.rating.registry import create_model as _create
from tests.conftest import make_vector

NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)


def fv(key, value, raw=None, prov="direct", weight=0.0):
    return FeatureValue(
        key=key,
        value=float(value),
        weight=float(weight),
        provenance_kind=prov,
        raw_value=raw,
    )


def build_vector(**items):
    features = {}
    for key, item in items.items():
        if isinstance(item, FeatureValue):
            features[key] = item
        else:
            value, raw = (item[0], item[1]) if isinstance(item, tuple) else (item, None)
            features[key] = fv(key, value, raw=raw)
    return FeatureVector(business_id="b", features=features)


def simple_config(keys, **extra):
    return RatingConfig(
        weights=normalize_weights(dict.fromkeys(keys, 1.0)),
        decay_half_life_days=14.0,
        **extra,
    )


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------


class TestNormalization:
    def test_binary_presence(self):
        assert normalize_binary("https://example.com") == 1.0
        assert normalize_binary("") == 0.0
        assert normalize_binary(None) == 0.0
        assert normalize_binary(0) == 0.0

    def test_score01_maps_bounded_range(self):
        assert normalize_score01(5.0) == pytest.approx(1.0)
        assert normalize_score01(1.0) == pytest.approx(0.0)
        assert normalize_score01(4.5) == pytest.approx(0.875)
        assert normalize_score01(None) == 0.0

    def test_score01_clamps_out_of_range(self):
        assert normalize_score01(6.0) == 1.0
        assert normalize_score01(0.0) == 0.0
        assert normalize_score01(-3.0) == 0.0

    def test_count_log_saturates(self):
        assert normalize_count_log(0) == 0.0
        assert normalize_count_log(-5) == 0.0
        assert normalize_count_log(None) == 0.0
        assert normalize_count_log(1000) == pytest.approx(1.0)
        assert normalize_count_log(1) > normalize_count_log(0)
        assert normalize_count_log(10000) == pytest.approx(1.0)

    def test_categorical_mapping(self):
        assert normalize_categorical("OPERATIONAL", {"OPERATIONAL": 1.0}, 0.0) == 1.0
        assert normalize_categorical("CLOSED", {"OPERATIONAL": 1.0}, 0.0) == 0.0
        assert normalize_categorical(None, {"OPERATIONAL": 1.0}, 0.0) == 0.0

    def test_passthrough_clamps(self):
        assert normalize_passthrough(0.5) == 0.5
        assert normalize_passthrough(2.0) == 1.0
        assert normalize_passthrough(-1.0) == 0.0

    def test_normalize_feature_falls_back_to_stored_value_without_raw(self):
        u = normalize_feature(
            "rating_score", fv("rating_score", 0.875), now=NOW, half_life_days=14.0
        )
        assert u == pytest.approx(0.875)

    def test_normalize_feature_uses_raw_when_present(self):
        u = normalize_feature(
            "rating_score", fv("rating_score", 0.0, raw=4.5), now=NOW, half_life_days=14.0
        )
        assert u == pytest.approx(0.875)

    def test_normalize_feature_passthrough_ignores_opaque_raw(self):
        u = normalize_feature(
            "website_quality",
            fv("website_quality", 0.7, raw={"word_count": 400, "title": "x"}),
            now=NOW,
            half_life_days=14.0,
        )
        assert u == pytest.approx(0.7)


# --------------------------------------------------------------------------
# Exponential time decay
# --------------------------------------------------------------------------


class TestTimeDecay:
    def test_t_zero_is_initial_magnitude(self):
        assert exponential_decay(0.0, 0.0495) == pytest.approx(1.0)

    def test_half_life_halves_signal(self):
        k = decay_rate_from_half_life(14.0)
        assert k == pytest.approx(0.69314718 / 14.0)
        assert exponential_decay(14.0, k) == pytest.approx(0.5, abs=1e-9)
        assert half_life(k) == pytest.approx(14.0)

    def test_decay_is_monotonic_decreasing(self):
        k = decay_rate_from_half_life(14.0)
        values = [exponential_decay(t, k) for t in (0, 1, 7, 30, 365)]
        assert all(values[i] > values[i + 1] for i in range(len(values) - 1))

    def test_negative_time_or_rate_clamps(self):
        assert exponential_decay(-10.0, 0.1) == pytest.approx(1.0)
        assert exponential_decay(10.0, -0.1) == pytest.approx(1.0)

    def test_days_between(self):
        earlier = NOW - timedelta(days=3, hours=12)
        assert days_between(earlier, NOW) == pytest.approx(3.5)

    def test_parse_timestamp(self):
        assert parse_timestamp(None) is None
        assert parse_timestamp("not-a-date") is None
        parsed = parse_timestamp("2026-08-09T12:00:00Z")
        assert parsed is not None and parsed.tzinfo is not None
        naive = parse_timestamp("2026-08-09T12:00:00")
        assert naive is not None and naive.tzinfo is not None


# --------------------------------------------------------------------------
# Transformations
# --------------------------------------------------------------------------


class TestTransform:
    def test_identity(self):
        assert transform_identity(0.4) == 0.4
        assert transform_identity(1.5) == 1.0

    def test_quadratic(self):
        assert transform_quadratic(1.0, 1.0, 0.0, 0.0) == 1.0
        assert transform_quadratic(0.2, 1.0, 0.0, 0.0) == pytest.approx(0.04)
        assert transform_quadratic(0.0, 1.0, 0.0, 0.0) == 0.0
        assert transform_quadratic(2.0, 1.0, 0.0, 0.0) == 1.0  # clamped

    def test_business_status_quadratic_is_justified(self):
        # OPERATIONAL stays 1.0; closed 0.2 collapses to 0.04.
        assert transform_feature("business_status", 1.0) == 1.0
        assert transform_feature("business_status", 0.2) == pytest.approx(0.04)

    def test_unknown_feature_identity(self):
        assert transform_feature("whatever", 0.6) == 0.6


# --------------------------------------------------------------------------
# Weighted rating engine
# --------------------------------------------------------------------------


class TestWeightedRatingModel:
    def test_rating_equals_weighted_sum_of_transformed(self):
        model = WeightedRatingModel(config=simple_config(["web_presence", "rating_score"]))
        vector = build_vector(web_presence=(1.0, "https://example.com"), rating_score=(0.0, 4.5))
        result = model.predict(vector, now=NOW)
        # w=0.5 each; web u=1 → 0.5, rating u=0.875 → 0.4375
        assert result.rating == pytest.approx(0.5 * 100 + 0.4375 * 100, abs=0.01)
        assert result.confidence == 1.0

    def test_uses_config_weights_not_vector_weights(self):
        model = WeightedRatingModel(config=simple_config(["web_presence"]))
        v = build_vector(web_presence=fv("web_presence", 1.0, raw="https://x.com", weight=999.0))
        result = model.predict(v, now=NOW)
        assert result.rating == pytest.approx(100.0, abs=0.01)

    def test_high_value_vector_scores_higher(self):
        model = WeightedRatingModel()
        good = model.predict(
            make_vector(dict.fromkeys("web_presence rating_score".split(), 1.0)), now=NOW
        )
        bad = model.predict(
            make_vector(dict.fromkeys("web_presence rating_score".split(), 0.0)), now=NOW
        )
        assert good.rating > bad.rating

    def test_breakdown_has_all_configured_features(self):
        model = WeightedRatingModel()
        result = model.predict(make_vector(), now=NOW)
        assert set(result.breakdown) == set(get_settings().feature_weights)

    def test_empty_vector_scores_zero(self):
        model = WeightedRatingModel()
        result = model.predict(FeatureVector(business_id="b"), now=NOW)
        assert result.rating == 0.0
        assert result.confidence == 0.0

    def test_missing_feature_contributes_zero_and_is_explained(self):
        model = WeightedRatingModel(config=simple_config(["web_presence", "rating_score"]))
        result = model.predict(build_vector(rating_score=(0.0, 4.5)), now=NOW)
        assert result.breakdown["web_presence"]["contribution"] == 0.0
        assert "no data" in result.breakdown["web_presence"]["explanation"]

    def test_unknown_extra_feature_is_ignored(self):
        model = WeightedRatingModel(config=simple_config(["web_presence"]))
        result = model.predict(
            build_vector(web_presence=(1.0, "https://x.com"), mystery=1.0), now=NOW
        )
        assert "mystery" not in result.breakdown

    def test_recent_activity_exponential_decay(self):
        model = WeightedRatingModel(config=simple_config(["recent_activity"]))
        today = build_vector(recent_activity=(0.0, NOW.isoformat()))
        two_weeks = build_vector(recent_activity=(0.0, (NOW - timedelta(days=14)).isoformat()))
        old = build_vector(recent_activity=(0.0, (NOW - timedelta(days=365)).isoformat()))
        assert model.predict(today, now=NOW).rating == pytest.approx(100.0, abs=1.0)
        assert model.predict(two_weeks, now=NOW).rating == pytest.approx(50.0, abs=0.5)
        assert model.predict(old, now=NOW).rating < 1.0

    def test_business_status_raw_operational(self):
        model = WeightedRatingModel(config=simple_config(["business_status"]))
        op = model.predict(build_vector(business_status=(0.0, "OPERATIONAL")), now=NOW)
        closed = model.predict(build_vector(business_status=(0.0, "CLOSED_PERMANENTLY")), now=NOW)
        assert op.rating == pytest.approx(100.0, abs=0.01)
        assert closed.rating == 0.0

    def test_rating_and_confidence_are_independent(self):
        model = WeightedRatingModel(config=simple_config(["web_presence", "rating_score"]))
        known = build_vector(web_presence=(0.8, None), rating_score=(0.8, None))
        unknown = build_vector(
            web_presence=fv("web_presence", 0.8, prov="unavailable"),
            rating_score=fv("rating_score", 0.8, prov="unavailable"),
        )
        rated_known = model.predict(known, now=NOW)
        rated_unknown = model.predict(unknown, now=NOW)
        # identical rating, different confidence
        assert rated_known.rating == pytest.approx(rated_unknown.rating)
        assert rated_known.confidence == 1.0
        assert rated_unknown.confidence == 0.0

    def test_deterministic_for_identical_input(self):
        model = WeightedRatingModel()
        vector = make_vector()
        first = model.predict(vector, now=NOW).to_dict()
        second = model.predict(vector, now=NOW).to_dict()
        assert first == second

    def test_fit_is_noop_and_requires_no_data(self):
        model = WeightedRatingModel()
        report = model.fit([])
        assert report.samples == 0
        assert not model.requires_fit()

    def test_status_exposes_config(self):
        status = WeightedRatingModel().status()
        assert status["name"] == "v1"
        assert "weights" in status
        assert "config" in status
        assert "decay" in status["config"]


# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------


class TestConfidence:
    def test_all_available_is_one(self):
        features = {k: fv(k, 0.5, prov="direct") for k in "abc"}
        assert confidence_from_features(features, {"a": 0.5, "b": 0.3, "c": 0.2}) == 1.0

    def test_all_unavailable_is_zero(self):
        features = {k: fv(k, 0.5, prov="unavailable") for k in "abc"}
        assert confidence_from_features(features, {"a": 0.5, "b": 0.3, "c": 0.2}) == 0.0

    def test_weighted_partial(self):
        features = {"a": fv("a", 1.0, prov="direct"), "b": fv("b", 1.0, prov="unavailable")}
        assert confidence_from_features(features, {"a": 0.75, "b": 0.25}) == pytest.approx(0.75)

    def test_missing_feature_counts_as_unavailable(self):
        assert confidence_from_features({}, {"a": 1.0}) == 0.0

    def test_label_bands(self):
        assert confidence_label(0.9) == "high"
        assert confidence_label(0.6) == "medium"
        assert confidence_label(0.2) == "low"


# --------------------------------------------------------------------------
# Explanations
# --------------------------------------------------------------------------


class TestExplanations:
    def test_format_contribution(self):
        assert format_contribution("Website presence", 30.0) == "Website presence: +30.0"
        assert format_contribution("Cost", -3.5) == "Cost: -3.5"

    def test_explain_feature_missing(self):
        entry = {"contribution": None, "provenance": "unavailable"}
        assert explain_feature("recent_activity", entry) == "Recent activity: no data (excluded)"

    def test_explain_feature_with_data(self):
        entry = {"contribution": 14.2, "provenance": "direct"}
        assert explain_feature("recent_activity", entry) == "Recent activity: +14.2"

    def test_build_explanation_has_total_and_unavailable_first(self):
        breakdown = {
            "web_presence": {"contribution": 18.0, "provenance": "direct"},
            "rating_score": {"contribution": 11.4, "provenance": "direct"},
            "recent_activity": {"contribution": None, "provenance": "unavailable"},
        }
        lines = build_explanation(breakdown, 29.4)
        assert lines[0] == "Recent activity: no data (excluded)"
        assert lines[-1] == "Total rating: 29.4 / 100"
        assert "Website presence: +18.0" in lines


# --------------------------------------------------------------------------
# Expected value
# --------------------------------------------------------------------------


class TestExpectedValue:
    def test_from_prior_computes_ev(self):
        ev = ExpectedValue.from_prior(0.15, 1000.0, 50.0)
        assert ev.ready
        assert ev.prob_state == "estimated"
        assert ev.expected_value == pytest.approx(100.0)

    def test_unknown_without_deal_value_or_cost(self):
        assert not ExpectedValue.from_prior(0.15, None, 50.0).ready
        assert not ExpectedValue.from_prior(0.15, 1000.0, None).ready
        assert not ExpectedValue.from_prior(None, 1000.0, 50.0).ready
        assert ExpectedValue.from_prior(0.15, None, 50.0).prob_state == "unknown"

    def test_observed_reserved_for_calibrated_data(self):
        ev = ExpectedValue.from_observed(0.4, 1000.0, 50.0)
        assert ev.prob_state == "observed"
        assert ev.expected_value == pytest.approx(350.0)

    def test_prior_out_of_range_is_unknown(self):
        ev = ExpectedValue.from_prior(2.5, 1000.0, 50.0)
        assert not ev.ready
        assert ev.prob_state == "unknown"


# --------------------------------------------------------------------------
# Centralized config
# --------------------------------------------------------------------------


class TestRatingConfig:
    def test_from_settings_normalizes_weights(self):
        cfg = RatingConfig.from_settings()
        assert sum(cfg.weights.values()) == pytest.approx(1.0)
        assert set(cfg.weights) == set(get_settings().feature_weights)

    def test_decay_rate_derived_from_half_life(self):
        cfg = RatingConfig.from_settings()
        assert cfg.decay_rate == pytest.approx(decay_rate_from_half_life(14.0))

    def test_transforms_declared(self):
        cfg = RatingConfig.from_settings()
        assert cfg.transforms["business_status"].kind == "quadratic"
        assert cfg.transforms["business_status"].a == 1.0

    def test_ev_fields_round_trip(self):
        cfg = simple_config(
            ["web_presence"], ev_prior_probability=0.2, ev_deal_value=500.0, ev_cost=10.0
        )
        assert cfg.ev_prior_probability == 0.2
        assert cfg.ev_deal_value == 500.0
        assert cfg.ev_cost == 10.0

    def test_normalize_weights_drops_non_positive(self):
        assert normalize_weights({"a": 2.0, "b": 0.0, "c": -1.0}) == {"a": 1.0}

    def test_normalize_weights_rejects_empty(self):
        with pytest.raises(ValueError):
            normalize_weights({"a": 0.0})


# --------------------------------------------------------------------------
# Registry + future interfaces
# --------------------------------------------------------------------------


class TestRegistryAndFuture:
    def test_create_v1_is_weighted_engine(self):
        assert isinstance(create_model("v1"), WeightedRatingModel)
        assert isinstance(_create("v1"), WeightedRatingModel)

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError):
            create_model("nope")

    def test_future_models_are_not_fake(self):
        for name in ("statistical", "probabilistic", "ml"):
            model = create_model(name)
            assert model.requires_fit()
            with pytest.raises(ModelNotImplemented):
                model.predict(make_vector())
            with pytest.raises(ModelNotImplemented):
                model.fit([])

    def test_future_classes(self):
        assert isinstance(create_model("statistical"), FutureStatisticalModel)
        assert isinstance(create_model("probabilistic"), FutureProbabilisticModel)
        assert isinstance(create_model("ml"), FutureMLModel)

    def test_available_models_include_all(self):
        names = available_models()
        for expected in ("v1", "v2", "v3", "v4", "statistical", "probabilistic", "ml"):
            assert expected in names
