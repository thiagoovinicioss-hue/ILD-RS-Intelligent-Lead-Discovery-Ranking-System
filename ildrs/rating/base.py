"""Rating model interface.

The pipeline only ever talks to a ``RatingModel``. This is the seam where the
system evolves from V1 (deterministic weights) to V2 (statistically
calibrated) to V3 (probability) to V4 (learned/adaptive) without touching the
pipeline, API, or frontend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ildrs.domain.entities import FeatureVector, RatingResult


@dataclass
class OutcomeSample:
    features: dict[str, float]  # feature key → normalized value
    outcome_value: int  # 0 or 1


@dataclass
class FitReport:
    model: str
    version: str
    samples: int
    method: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
    message: str = ""


class RatingModelError(RuntimeError):
    """Base class for rating model failures."""


class ModelNotReadyError(RatingModelError):
    """The model cannot predict because it needs calibration/data first."""


class ModelNotImplemented(RatingModelError):
    """The model variant is defined but intentionally not implemented yet."""


@runtime_checkable
class RatingModel(Protocol):
    name: str
    version: str

    def predict(self, features: FeatureVector) -> RatingResult:
        """Compute R = f(X), rating scaled to 0–100."""
        ...

    def fit(self, samples: list[OutcomeSample]) -> FitReport:
        """Learn/calibrate from historical outcomes."""
        ...

    def requires_fit(self) -> bool:
        """True when the model needs historical data before predicting."""
        ...

    def status(self) -> dict:
        """Human-readable status for observability."""
        ...


def scale_to_100(raw: float) -> float:
    """Scale a [0, 1] score to the 0–100 rating scale, clamped."""
    return max(0.0, min(100.0, raw * 100.0))
