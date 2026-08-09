"""V2 — statistically calibrated weights.

Weights are calibrated from historical outcomes using the point-biserial
correlation between each feature and the binary outcome
(response/interest/conversion vs. no response). This is transparent
classical statistics — not machine learning — and only activates once a
minimum number of outcomes has been recorded.

    rᵢ = (x̄₁ − x̄₀) / sₓ · √(p(1−p))
    wᵢ ∝ max(0, rᵢ)

If no feature has a positive correlation, the model falls back to the
configured V1 weights.
"""

from __future__ import annotations

import math

from ildrs.config import get_settings
from ildrs.domain.entities import FeatureVector, RatingResult
from ildrs.rating.base import (
    FitReport,
    ModelNotReadyError,
    OutcomeSample,
    scale_to_100,
)
from ildrs.rating.ev import ExpectedValue
from ildrs.rating.weighted import WeightedScoringModel, normalize_weights

_MIN_N = 2  # absolute floor for a usable mean; real floor comes from config


def point_biserial(x: list[float], y: list[int]) -> float:
    """Point-biserial correlation coefficient between continuous x and binary y."""
    n = len(x)
    if n < _MIN_N:
        return 0.0
    p1 = sum(y) / n  # fraction with outcome == 1
    if p1 in (0.0, 1.0):
        return 0.0
    mean_x = sum(x) / n
    mean_1 = sum(xi for xi, yi in zip(x, y, strict=True) if yi == 1) / max(1, int(p1 * n))
    mean_0 = sum(xi for xi, yi in zip(x, y, strict=True) if yi == 0) / max(1, n - int(p1 * n))
    var = sum((xi - mean_x) ** 2 for xi in x) / n
    s = math.sqrt(var)
    if s < 1e-12:
        return 0.0
    return (mean_1 - mean_0) / s * math.sqrt(p1 * (1 - p1))


class CalibratedWeightsModel:
    """Statistical weight calibration (V2)."""

    name = "v2"
    version = "v2.0"

    def __init__(
        self, weights: dict[str, float] | None = None, min_samples: int | None = None
    ) -> None:
        settings = get_settings()
        self._fallback_weights = normalize_weights(weights or settings.feature_weights)
        self._min_samples = min_samples if min_samples is not None else settings.rating_min_samples
        self._fitted = False
        self._samples = 0
        self._weights: dict[str, float] = self._fallback_weights
        self._correlations: dict[str, float] = {}

    def fit(self, samples: list[OutcomeSample]) -> FitReport:
        if len(samples) < self._min_samples:
            raise ModelNotReadyError(
                f"V2 requires at least {self._min_samples} historical outcomes "
                f"to calibrate (have {len(samples)})."
            )

        keys = list(self._fallback_weights)
        correlations: dict[str, float] = {}
        for key in keys:
            xs: list[float] = []
            ys: list[int] = []
            for sample in samples:
                value = sample.features.get(key)
                if value is None or not math.isfinite(value):
                    continue
                xs.append(float(value))
                ys.append(int(sample.outcome_value))
            correlations[key] = point_biserial(xs, ys)

        positive = {k: max(0.0, r) for k, r in correlations.items()}
        total = sum(positive.values())
        if total <= 1e-12:
            weights = self._fallback_weights
            message = "no feature shows a positive correlation; kept configured weights."
        else:
            weights = {k: v / total for k, v in positive.items()}
            message = "weights calibrated from point-biserial correlations."

        self._correlations = correlations
        self._weights = weights
        self._samples = len(samples)
        self._fitted = True
        return FitReport(
            model=self.name,
            version=self.version,
            samples=len(samples),
            method="point-biserial correlation",
            metadata={
                "correlations": {k: round(v, 4) for k, v in correlations.items()},
                "weights": {k: round(v, 4) for k, v in weights.items()},
            },
            message=message,
        )

    def predict(self, features: FeatureVector) -> RatingResult:
        if not self._fitted:
            raise ModelNotReadyError(
                "V2 model is not calibrated yet. Record historical outcomes and run "
                "`ildrs rate --fit` (requires ILD_RATING_MIN_SAMPLES outcomes)."
            )
        breakdown: dict[str, dict] = {}
        total = 0.0
        for key, value in features.features.items():
            w = self._weights.get(key, 0.0)
            contribution = value.value * w
            total += contribution
            breakdown[key] = {
                "value": round(value.value, 4),
                "weight": round(w, 4),
                "contribution": round(contribution, 4),
                "provenance": value.provenance_kind,
            }
        ev_cfg = get_settings()
        expected_value = ExpectedValue.from_prior(
            ev_cfg.ev_prior_probability, ev_cfg.ev_deal_value, ev_cfg.ev_cost
        )
        return RatingResult(
            rating=scale_to_100(total),
            confidence=0.0,
            model=self.name,
            model_version=self.version,
            breakdown=breakdown,
            metadata={
                "method": "statistically calibrated weights",
                "calibrated_on": self._samples,
                "correlations": {k: round(v, 4) for k, v in self._correlations.items()},
                "expected_value": expected_value.to_dict(),
            },
        )

    def requires_fit(self) -> bool:
        return True

    def status(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "fitted": self._fitted,
            "samples": self._samples,
            "min_samples": self._min_samples,
            "weights": {k: round(v, 4) for k, v in self._weights.items()},
            "correlations": {k: round(v, 4) for k, v in self._correlations.items()},
        }

    @property
    def is_fitted(self) -> bool:
        return self._fitted


class UncalibratedFallback(WeightedScoringModel):
    """Decorator-style fallback used by the pipeline when the configured model
    requires fit but has not been calibrated: predict with V1 while flagging
    that V2 is not active."""

    def __init__(self, target_name: str, weights: dict[str, float] | None = None) -> None:
        super().__init__(weights=weights)
        self.target_name = target_name

    def predict(self, features: FeatureVector) -> RatingResult:
        result = super().predict(features)
        result.metadata["fallback"] = (
            f"model '{self.target_name}' not calibrated; predicted with V1 weights."
        )
        return result
