"""V3 — probability of response/conversion (interface only).

Defined as the next evolution stage. Deliberately not implemented: per the
project brief, machine-learning stages are added later. The interface exists
so the pipeline and API can already handle it.
"""

from __future__ import annotations

from ildrs.domain.entities import FeatureVector, RatingResult
from ildrs.rating.base import FitReport, ModelNotImplemented, OutcomeSample


class ProbabilisticModel:
    name = "v3"
    version = "v3.0"

    def predict(self, features: FeatureVector) -> RatingResult:
        raise ModelNotImplemented(
            "V3 (probability of response) is defined but not implemented yet. "
            "It will be enabled once sufficient historical outcomes are collected."
        )

    def fit(self, samples: list[OutcomeSample]) -> FitReport:
        raise ModelNotImplemented(
            "V3 requires a probabilistic calibration procedure that is not "
            "implemented in this release."
        )

    def requires_fit(self) -> bool:
        return True

    def status(self) -> dict:
        return {"name": self.name, "version": self.version, "implemented": False}
