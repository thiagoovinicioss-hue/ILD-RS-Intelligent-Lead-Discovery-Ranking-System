"""Centralized rating-model configuration.

Weights, decay parameters, transformations, and EV assumptions all live here —
never scattered across source files. Every numeric constant is either read from
environment configuration (weights, decay half-life, EV prior) or declared as a
documented hypothesis in the feature/transform specs.

The initial weight values are hypotheses to be revised from real outcomes, not
scientific truths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ildrs.rating.decay import decay_rate_from_half_life
from ildrs.rating.spec import FEATURE_ORDER, assert_spec_keys_match_config
from ildrs.rating.transform import TRANSFORM_SPECS, TransformSpec

WEIGHT_EPS = 1e-9


def normalize_weights(raw: dict[str, float], order: list[str] | None = None) -> dict[str, float]:
    """Normalize weights to sum 1, dropping non-positive values.

    The result follows ``order`` (default: the canonical feature order) so the
    engine iterates deterministically regardless of dict construction.
    """
    cleaned = {k: float(v) for k, v in raw.items() if v is not None and float(v) > 0}
    total = sum(cleaned.values())
    if total <= WEIGHT_EPS:
        raise ValueError("feature weights must be positive and sum to > 0")
    normalized = {k: v / total for k, v in cleaned.items()}
    order = order or FEATURE_ORDER
    ordered = {k: normalized[k] for k in order if k in normalized}
    ordered.update({k: normalized[k] for k in normalized if k not in ordered})
    return ordered


@dataclass(frozen=True)
class RatingConfig:
    weights: dict[str, float]
    decay_half_life_days: float
    transforms: dict[str, TransformSpec] = field(default_factory=dict)
    ev_prior_probability: float | None = None
    ev_deal_value: float | None = None
    ev_cost: float | None = None

    @classmethod
    def from_settings(cls, settings: Any | None = None) -> RatingConfig:
        from ildrs.config import get_settings

        settings = settings or get_settings()
        assert_spec_keys_match_config()
        weights = normalize_weights(settings.feature_weights)
        return cls(
            weights=weights,
            decay_half_life_days=float(settings.rating_decay_half_life_days),
            transforms=dict(TRANSFORM_SPECS),
            ev_prior_probability=settings.ev_prior_probability,
            ev_deal_value=settings.ev_deal_value,
            ev_cost=settings.ev_cost,
        )

    @property
    def decay_rate(self) -> float:
        """k = ln(2) / t½, the coefficient in A(t) = A0·exp(−k·t)."""
        return decay_rate_from_half_life(self.decay_half_life_days)

    def summary(self) -> dict[str, Any]:
        return {
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "decay": {
                "model": "A(t) = A0·exp(−k·t)",
                "half_life_days": self.decay_half_life_days,
                "k_per_day": round(self.decay_rate, 6),
            },
            "transforms": {
                key: {
                    "kind": spec.kind,
                    "a": spec.a,
                    "b": spec.b,
                    "c": spec.c,
                    "justification": spec.justification,
                }
                for key, spec in self.transforms.items()
            },
            "expected_value": {
                "formula": "EV = P(conversion)·value − cost",
                "prior_probability": self.ev_prior_probability,
                "deal_value": self.ev_deal_value,
                "cost": self.ev_cost,
            },
        }
