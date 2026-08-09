"""Feature validation.

Validates a feature vector for:
- schema completeness (all defined keys present)
- value ranges
- weight normalization (sum of weights ≈ 1)
- availability (how much data is real vs unavailable)

Produces a report used by the pipeline to gate rating and to compute the
lead confidence score.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ildrs.domain.entities import FeatureVector
from ildrs.features.definitions import feature_definitions


@dataclass
class ValidationReport:
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    availability: float = 0.0  # share of weighted data that is available
    feature_count: int = 0

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "availability": round(self.availability, 4),
            "feature_count": self.feature_count,
        }


class FeatureValidator:
    def __init__(self) -> None:
        self.definitions = feature_definitions()

    def validate(self, vector: FeatureVector) -> ValidationReport:
        report = ValidationReport()
        report.feature_count = len(vector.features)

        for key, definition in self.definitions.items():
            if key not in vector.features:
                report.errors.append(f"missing feature '{key}'")
                continue
            value = vector.features[key]
            if not (definition.range[0] <= value.value <= definition.range[1]):
                report.errors.append(
                    f"feature '{key}' value {value.value} out of range {definition.range}"
                )
            if value.weight <= 0:
                report.warnings.append(f"feature '{key}' has non-positive weight")

        for key in vector.features:
            if key not in self.definitions:
                report.errors.append(f"unknown feature '{key}'")

        total_weight = sum(v.weight for v in vector.features.values())
        if abs(total_weight - 1.0) > 1e-6 and vector.features:
            report.errors.append(f"weights do not sum to 1 (sum={total_weight:.6f})")

        report.availability = self._availability(vector)
        report.valid = not report.errors
        return report

    @staticmethod
    def _availability(vector: FeatureVector) -> float:
        """Weighted share of features whose provenance is available."""
        available = 0.0
        total = 0.0
        for value in vector.features.values():
            total += value.weight
            if value.provenance_kind != "unavailable":
                available += value.weight
        return (available / total) if total > 0 else 0.0
