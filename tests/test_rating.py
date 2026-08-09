"""Tests for rating models (Architecture §5)."""

from __future__ import annotations

import pytest

from ildrs.rating.base import ModelNotReadyError, OutcomeSample, scale_to_100
from ildrs.rating.calibrated import CalibratedWeightsModel, point_biserial
from ildrs.rating.weighted import WeightedScoringModel
from tests.conftest import make_vector


def test_scale_to_100_clamps():
    assert scale_to_100(0.0) == 0.0
    assert scale_to_100(1.0) == 100.0
    assert 50.0 <= scale_to_100(0.5) <= 51.0


class TestWeightedScoringModel:
    def test_rating_equals_weighted_sum(self):
        model = WeightedScoringModel()
        vector = make_vector()
        result = model.predict(vector)
        expected = sum(f.value * f.weight for f in vector.features.values()) * 100.0
        assert result.rating == pytest.approx(expected, abs=0.01)
        assert result.model == "v1"

    def test_breakdown_has_all_features(self):
        model = WeightedScoringModel()
        result = model.predict(make_vector())
        assert set(result.breakdown) == set(make_vector().features)

    def test_high_value_vector_scores_higher(self):
        model = WeightedScoringModel()
        good = model.predict(
            make_vector(dict.fromkeys("web_presence rating_score review_volume".split(), 1.0))
        )
        bad = model.predict(
            make_vector(dict.fromkeys("web_presence rating_score review_volume".split(), 0.0))
        )
        assert good.rating > bad.rating


class TestPointBiserial:
    def test_positive_correlation(self):
        r = point_biserial([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1])
        assert r > 0.5

    def test_negative_correlation(self):
        r = point_biserial([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1])
        assert r < -0.5

    def test_constant_x_returns_zero(self):
        assert point_biserial([0.5, 0.5, 0.5, 0.5], [0, 0, 1, 1]) == 0.0

    def test_constant_y_returns_zero(self):
        assert point_biserial([0.1, 0.2, 0.3, 0.4], [1, 1, 1, 1]) == 0.0


class TestCalibratedWeightsModel:
    def _samples(self, n: int = 10) -> list[OutcomeSample]:
        samples = []
        for i in range(n):
            high = i >= n // 2
            values = {
                "web_presence": 1.0 if high else 0.0,
                "rating_score": 0.9 if high else 0.3,
                "review_volume": 0.8 if high else 0.2,
                "business_status": 1.0,
                "contact_availability": 0.7,
                "category_fit": 0.6,
                "location_fit": 0.5,
            }
            samples.append(OutcomeSample(features=values, outcome_value=1 if high else 0))
        return samples

    def test_fit_requires_min_samples(self):
        model = CalibratedWeightsModel(min_samples=5)
        with pytest.raises(ModelNotReadyError):
            model.fit(self._samples(2))

    def test_fit_calibrates_from_outcomes(self):
        model = CalibratedWeightsModel(min_samples=5)
        report = model.fit(self._samples(10))
        assert report.model == "v2"
        assert report.samples == 10
        # high-outcome features must carry positive weight
        assert model._weights["rating_score"] > 0
        assert model._weights["web_presence"] > 0

    def test_predict_requires_fit(self):
        model = CalibratedWeightsModel(min_samples=5)
        with pytest.raises(ModelNotReadyError):
            model.predict(make_vector())

    def test_predict_emits_expected_value_metadata(self):
        model = CalibratedWeightsModel(min_samples=5)
        model.fit(self._samples(10))
        result = model.predict(make_vector())
        assert result.metadata["expected_value"]["prob_state"] in ("estimated", "unknown")
        assert "expected_value" in result.metadata

    def test_no_positive_correlation_keeps_fallback(self):
        model = CalibratedWeightsModel(min_samples=2, weights={"a": 1.0, "b": 0.0})
        samples = [
            OutcomeSample(features={"a": 0.0, "b": 0.0}, outcome_value=1),
            OutcomeSample(features={"a": 1.0, "b": 0.0}, outcome_value=0),
        ]
        report = model.fit(samples)
        # normalize_weights drops the zero-weight key
        assert model._weights == {"a": 1.0}
        assert "positive correlation" in report.message
