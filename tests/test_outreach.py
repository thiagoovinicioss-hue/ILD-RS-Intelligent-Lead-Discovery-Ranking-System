"""Tests for the outreach workflow (Architecture §5/§6)."""

from __future__ import annotations

from ildrs.outreach.workflow import _feature_snapshot, outcome_to_sample
from ildrs.rating.weighted import WeightedRatingModel
from tests.conftest import make_vector


class _Row:
    def __init__(self, features):
        self.features = features
        self.outcome_value = 1


def test_feature_snapshot_extracts_per_feature_values():
    result = WeightedRatingModel().predict(make_vector())
    snapshot = _feature_snapshot(result.to_dict())
    # The snapshot must be keyed by feature name, not the rating-result envelope.
    assert "web_presence" in snapshot
    assert "rating" not in snapshot
    assert "confidence" not in snapshot
    assert "breakdown" not in snapshot
    assert snapshot["web_presence"] == 1.0


def test_feature_snapshot_passthrough_when_not_envelope():
    assert _feature_snapshot({"a": 1.0, "b": 0.5}) == {"a": 1.0, "b": 0.5}
    assert _feature_snapshot(None) == {}
    assert _feature_snapshot([]) == {}


def test_outcome_to_sample_uses_snapshot_values():
    result = WeightedRatingModel().predict(make_vector())
    sample = outcome_to_sample(_Row(_feature_snapshot(result.to_dict())))
    assert sample["outcome_value"] == 1
    assert sample["features"]["web_presence"] == 1.0
    assert set(sample["features"]) >= {"web_presence", "rating_score", "review_volume"}
