"""V1 — the weighted rating engine (mathematical core).

Pipeline per feature:

    raw value → normalize → transform → weighted contribution

    R = 100 · Σᵢ wᵢ · zᵢ            zᵢ = transform_i(normalize_i(xᵢ))

Per-feature contributions and the total rating share the same 0–100 scale, so
each explanation line adds up to the total (e.g. ``Website presence: +18.0``).

Everything is:

- **normalized** per feature type (binary / bounded score / log count /
  categorical / passthrough / exponential time decay) before summing —
  incompatible raw values are never added blindly;
- **transformed** nonlinearly only where justified (see ``transform``);
- **weighted** by the centralized :class:`RatingConfig` weights;
- **explained** — every contribution is emitted with a human-readable line
  and a total, so no rating is ever an unexplained number;
- **accompanied by confidence** — a separate number describing data coverage,
  never confused with the rating itself.

Deterministic: identical input (with a fixed clock) yields identical output.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ildrs.domain.entities import FeatureVector, RatingResult
from ildrs.rating.base import FitReport, OutcomeSample
from ildrs.rating.confidence import confidence_from_features, confidence_label
from ildrs.rating.config import RatingConfig, normalize_weights
from ildrs.rating.ev import ExpectedValue
from ildrs.rating.explain import build_explanation, explain_feature
from ildrs.rating.normalize import normalize_feature
from ildrs.rating.spec import FEATURE_SPECS
from ildrs.rating.transform import transform_feature

__all__ = ["WeightedRatingModel", "WeightedScoringModel", "normalize_weights"]


class WeightedRatingModel:
    """Deterministic weighted rating engine (V1)."""

    name = "v1"
    version = "v1.2"

    def __init__(
        self,
        config: RatingConfig | None = None,
        weights: dict[str, float] | None = None,
    ) -> None:
        if config is not None and weights is not None:
            raise ValueError("pass either config or weights, not both")
        if weights is not None:
            config = RatingConfig(
                weights=normalize_weights(weights),
                decay_half_life_days=14.0,
                transforms={},
            )
        self.config = config if config is not None else RatingConfig.from_settings()

    # -- RatingModel interface ------------------------------------------

    def predict(self, features: FeatureVector, *, now: datetime | None = None) -> RatingResult:
        now = now or datetime.now(UTC)
        cfg = self.config
        breakdown: dict[str, dict[str, Any]] = {}
        total = 0.0

        for key in cfg.weights:
            fv = features.features.get(key)
            if fv is None:
                entry = {
                    "value": 0.0,
                    "normalized": 0.0,
                    "transformed": 0.0,
                    "weight": round(cfg.weights[key], 4),
                    "contribution": 0.0,
                    "provenance": "unavailable",
                    "raw_value": None,
                    "label": FEATURE_SPECS[key].label if key in FEATURE_SPECS else key,
                }
                entry["explanation"] = explain_feature(key, entry)
                breakdown[key] = entry
                continue

            u = normalize_feature(key, fv, now=now, half_life_days=cfg.decay_half_life_days)
            z = transform_feature(key, u)
            weight = cfg.weights[key]
            contribution = weight * z * 100.0  # rating points, same scale as total
            total += contribution

            entry = {
                "value": round(u, 4),
                "normalized": round(u, 4),
                "transformed": round(z, 4),
                "weight": round(weight, 4),
                "contribution": round(contribution, 4),
                "provenance": fv.provenance_kind,
                "raw_value": _raw_preview(fv.raw_value),
                "label": FEATURE_SPECS[key].label if key in FEATURE_SPECS else key,
            }
            entry["explanation"] = explain_feature(key, entry)
            breakdown[key] = entry

        rating = max(0.0, min(100.0, total))  # total is already in rating points
        confidence = confidence_from_features(features.features, cfg.weights)
        ev = ExpectedValue.from_prior(cfg.ev_prior_probability, cfg.ev_deal_value, cfg.ev_cost)

        return RatingResult(
            rating=rating,
            confidence=confidence,
            model=self.name,
            model_version=self.version,
            breakdown=breakdown,
            metadata={
                "method": "weighted sum after per-feature normalization + transform",
                "formula": "R = 100 · Σ wᵢ · transform_i(normalize_i(xᵢ))",
                "confidence": {
                    "label": confidence_label(confidence),
                    "basis": "weighted data availability",
                },
                "explanations": build_explanation(breakdown, rating),
                "expected_value": ev.to_dict(),
                "config": cfg.summary(),
            },
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
            "weights": {k: round(v, 4) for k, v in self.config.weights.items()},
            "config": self.config.summary(),
        }


# Backward-compatible alias (previous V1 class name).
WeightedScoringModel = WeightedRatingModel


def _raw_preview(raw: Any) -> Any:
    """Compact, JSON-safe preview of a raw value for the breakdown."""
    if raw is None or isinstance(raw, (str, int, float, bool)):
        return raw if not isinstance(raw, str) else raw[:120]
    if isinstance(raw, dict):
        return f"<dict:{len(raw)} keys>"
    if isinstance(raw, (list, tuple)):
        return f"<{type(raw).__name__}:{len(raw)} items>"
    return str(raw)[:120]
