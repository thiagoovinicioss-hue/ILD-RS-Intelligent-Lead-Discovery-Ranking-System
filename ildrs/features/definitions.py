"""Feature definitions.

Every feature is documented, has a default weight, a value range, and a
provenance policy. The feature vector X = (x1, …, xn) is built exclusively
from these definitions.
"""

from __future__ import annotations

from dataclasses import dataclass

from ildrs.config import DEFAULT_WEIGHTS, FEATURE_KEYS


@dataclass(frozen=True)
class FeatureDefinition:
    key: str
    label: str
    description: str
    range: tuple[float, float] = (0.0, 1.0)
    # which business field drives its provenance ("rating", "review_count", …)
    provenance_field: str = ""

    @property
    def default_weight(self) -> float:
        return DEFAULT_WEIGHTS.get(self.key, 0.0)


FEATURE_DEFINITIONS: dict[str, FeatureDefinition] = {
    feat.key: feat
    for feat in (
        FeatureDefinition(
            "web_presence",
            "Web presence",
            "Has an indexable website with a real domain.",
            provenance_field="website",
        ),
        FeatureDefinition(
            "rating_score",
            "Rating score",
            "Provider rating mapped from 1–5 to 0–1.",
            provenance_field="rating",
        ),
        FeatureDefinition(
            "review_volume",
            "Review volume",
            "Log-scaled review count (saturates at 1000).",
            provenance_field="review_count",
        ),
        FeatureDefinition(
            "business_status",
            "Business status",
            "Operational status (1.0 operational, 0.2 otherwise).",
            provenance_field="business_status",
        ),
        FeatureDefinition(
            "contact_availability",
            "Contact availability",
            "Share of contact channels (phone/website/email) present.",
            provenance_field="contact",
        ),
        FeatureDefinition(
            "category_fit",
            "Category fit",
            "Match between business category and configured target categories.",
            provenance_field="category",
        ),
        FeatureDefinition(
            "location_fit",
            "Location fit",
            "Proximity to configured discovery center, exponential decay.",
            provenance_field="location",
        ),
    )
}

FEATURE_KEYS_CHECK = tuple(FEATURE_DEFINITIONS.keys())


def feature_definitions() -> dict[str, FeatureDefinition]:
    return dict(FEATURE_DEFINITIONS)


def assert_keys_match_config() -> None:
    configured = set(FEATURE_KEYS)
    defined = set(FEATURE_DEFINITIONS)
    if configured != defined:
        raise RuntimeError(
            f"feature/config key mismatch: config={configured} definitions={defined}"
        )
