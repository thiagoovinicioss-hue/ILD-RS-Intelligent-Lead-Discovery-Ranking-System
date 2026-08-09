"""V4 — adaptive / learned ranking (interface only).

Defined as the final evolution stage. Deliberately not implemented; the
architecture reserves this seam for a future learned model without touching
the rest of the system.
"""

from __future__ import annotations

from ildrs.domain.entities import FeatureVector, RatingResult
from ildrs.rating.base import FitReport, ModelNotImplemented, OutcomeSample


class AdaptiveRankingModel:
    name = "v4"
    version = "v4.0"

    def predict(self, features: FeatureVector) -> RatingResult:
        raise ModelNotImplemented(
            "V4 (adaptive/learned ranking) is defined but not implemented yet."
        )

    def fit(self, samples: list[OutcomeSample]) -> FitReport:
        raise ModelNotImplemented(
            "V4 requires a learned ranking procedure that is not implemented in this release."
        )

    def requires_fit(self) -> bool:
        return True

    def status(self) -> dict:
        return {"name": self.name, "version": self.version, "implemented": False}
