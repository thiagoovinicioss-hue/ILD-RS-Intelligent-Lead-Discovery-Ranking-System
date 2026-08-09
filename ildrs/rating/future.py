"""Future rating-model interfaces.

The architecture reserves these seams for later evolution. They are declared
but intentionally NOT implemented — the system must not fake ML. Each one
raises :class:`ModelNotImplemented` until real methodology is added:

- ``FutureStatisticalModel``  — weight/parameter calibration from outcomes
- ``FutureProbabilisticModel`` — P(conversion) as a calibrated probability
- ``FutureMLModel``           — a learned ranking model from historical data

All implement the same :class:`RatingModel` protocol, so wiring them in never
touches the pipeline, API, or frontend.
"""

from __future__ import annotations

from ildrs.domain.entities import FeatureVector, RatingResult
from ildrs.rating.base import FitReport, ModelNotImplemented, OutcomeSample


class FutureStatisticalModel:
    name = "statistical"
    version = "0.0"

    def predict(self, features: FeatureVector) -> RatingResult:
        raise ModelNotImplemented(
            "The future statistical model is defined but not implemented yet. "
            "It will calibrate weights from historical outcomes."
        )

    def fit(self, samples: list[OutcomeSample]) -> FitReport:
        raise ModelNotImplemented(
            "The future statistical model requires a calibration procedure that "
            "is not implemented in this release."
        )

    def requires_fit(self) -> bool:
        return True

    def status(self) -> dict:
        return {"name": self.name, "version": self.version, "implemented": False}


class FutureProbabilisticModel:
    name = "probabilistic"
    version = "0.0"

    def predict(self, features: FeatureVector) -> RatingResult:
        raise ModelNotImplemented(
            "The future probabilistic model is defined but not implemented yet. "
            "It will emit P(conversion) calibrated from outcomes."
        )

    def fit(self, samples: list[OutcomeSample]) -> FitReport:
        raise ModelNotImplemented(
            "The future probabilistic model requires a probabilistic calibration "
            "procedure that is not implemented in this release."
        )

    def requires_fit(self) -> bool:
        return True

    def status(self) -> dict:
        return {"name": self.name, "version": self.version, "implemented": False}


class FutureMLModel:
    name = "ml"
    version = "0.0"

    def predict(self, features: FeatureVector) -> RatingResult:
        raise ModelNotImplemented(
            "The future ML model is defined but not implemented yet. "
            "It will learn a ranking function from historical data."
        )

    def fit(self, samples: list[OutcomeSample]) -> FitReport:
        raise ModelNotImplemented(
            "The future ML model requires a learned ranking procedure that is "
            "not implemented in this release."
        )

    def requires_fit(self) -> bool:
        return True

    def status(self) -> dict:
        return {"name": self.name, "version": self.version, "implemented": False}
