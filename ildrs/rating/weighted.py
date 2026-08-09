"""V1 — deterministic weighted scoring.

    R = 100 · Σᵢ wᵢ·xᵢ

Weights are taken from configuration (normalized to sum 1) and are fully
transparent. No historical data is required.
"""

from __future__ import annotations

from ildrs.config import get_settings
from ildrs.domain.entities import FeatureVector, RatingResult
from ildrs.rating.base import FitReport, OutcomeSample, scale_to_100

WEIGHT_EPS = 1e-9


class WeightedScoringModel:
    """Deterministic linear rating model (V1)."""

    name = "v1"
    version = "v1.0"

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        settings = get_settings()
        raw = dict(weights) if weights is not None else settings.feature_weights
        self.weights = normalize_weights(raw)

    def predict(self, features: FeatureVector) -> RatingResult:
        breakdown: dict[str, dict] = {}
        total = 0.0
        for key, value in features.features.items():
            w = self.weights.get(key, 0.0)
            contribution = value.value * w
            total += contribution
            breakdown[key] = {
                "value": round(value.value, 4),
                "weight": round(w, 4),
                "contribution": round(contribution, 4),
                "provenance": value.provenance_kind,
            }
        return RatingResult(
            rating=scale_to_100(total),
            confidence=0.0,  # filled by the pipeline via validation
            model=self.name,
            model_version=self.version,
            breakdown=breakdown,
            metadata={"method": "deterministic weighted sum"},
        )

    def fit(self, samples: list[OutcomeSample]) -> FitReport:
        # V1 has no free parameters; fitting is a no-op by design.
        return FitReport(
            model=self.name,
            version=self.version,
            samples=len(samples),
            method="none",
            message="V1 is deterministic; no calibration is needed.",
        )

    def requires_fit(self) -> bool:
        return False

    def status(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
        }


def normalize_weights(raw: dict[str, float]) -> dict[str, float]:
    """Normalize weights to sum 1, dropping non-positive or unknown keys."""
    cleaned = {k: float(v) for k, v in raw.items() if v and v > 0}
    total = sum(cleaned.values())
    if total <= WEIGHT_EPS:
        raise ValueError("feature weights must be positive and sum to > 0")
    return {k: v / total for k, v in cleaned.items()}
