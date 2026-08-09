"""Rating model factory — the only place that maps names to implementations."""

from __future__ import annotations

from ildrs.rating.adaptive import AdaptiveRankingModel
from ildrs.rating.base import RatingModel
from ildrs.rating.calibrated import CalibratedWeightsModel
from ildrs.rating.probabilistic import ProbabilisticModel
from ildrs.rating.weighted import WeightedScoringModel

_VARIANTS = {
    "v1": WeightedScoringModel,
    "v2": CalibratedWeightsModel,
    "v3": ProbabilisticModel,
    "v4": AdaptiveRankingModel,
}


def create_model(name: str | None = None) -> RatingModel:
    variant = (name or "v1").strip().lower()
    factory = _VARIANTS.get(variant)
    if factory is None:
        raise ValueError(f"unknown rating model '{variant}'")
    return factory()


def available_models() -> list[str]:
    return list(_VARIANTS)


__all__ = ["create_model", "available_models", "RatingModel"]
