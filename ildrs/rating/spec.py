"""Feature specs for the rating engine.

Each feature declares how its raw value is normalized and whether it is
transformed nonlinearly. This is the single source of truth the engine reads
to turn ``X = (x1, …, xn)`` into a comparable, compatible vector.

Normalization kinds:
    binary        → raw presence ⇒ {0.0, 1.0}
    score01       → bounded provider score mapped to [0, 1]
    count_log     → counts compressed with log10 (saturating)
    categorical   → discrete value mapped through a documented table
    passthrough   → value is already normalized by the pipeline (derived score)
    time_decay    → exponential decay A(t) = A0·exp(−k·t) on the raw timestamp

Transform kinds:
    identity      → z = u
    quadratic     → z = a·u² + b·u + c  (only where justified, see spec)
"""

from __future__ import annotations

from dataclasses import dataclass

from ildrs.config import FEATURE_KEYS

# provider score 1–5 is mapped linearly onto [0, 1]
RATING_LO = 1.0
RATING_HI = 5.0

# review counts span orders of magnitude; log compression saturates at this cap
REVIEW_CAP = 1000

# discrete business-status mapping (matches the pipeline's documented heuristic)
STATUS_MAPPING = {"OPERATIONAL": 1.0}


@dataclass(frozen=True)
class FeatureSpec:
    key: str
    label: str
    normalize: str
    transform: str
    description: str = ""
    justification: str = ""


FEATURE_SPECS: dict[str, FeatureSpec] = {
    spec.key: spec
    for spec in (
        FeatureSpec(
            "web_presence",
            "Website presence",
            "binary",
            "identity",
            "Has an indexable website with a real domain.",
            "Presence is a yes/no fact; summing a URL string into a weighted sum "
            "would be meaningless, so it is binarized to {0, 1}.",
        ),
        FeatureSpec(
            "rating_score",
            "Rating score",
            "score01",
            "identity",
            "Provider rating mapped from 1–5 to [0, 1].",
            "Bounded provider score; normalized to a range so it is comparable "
            "with the other features.",
        ),
        FeatureSpec(
            "review_volume",
            "Review volume",
            "count_log",
            "identity",
            "Log-scaled review count (saturates at 1000).",
            "Raw review counts are heavy-tailed (10 vs 1000); log compression "
            "keeps the spread compatible with other [0, 1] features.",
        ),
        FeatureSpec(
            "business_status",
            "Business status",
            "categorical",
            "quadratic",
            "Operational status (1.0 operational, 0.2 otherwise).",
            "Nonlinear: a permanently closed business is worth far less than "
            "the linear 0.2 suggests, so z = u² collapses 0.2 → 0.04 while "
            "leaving OPERATIONAL (1.0) untouched.",
        ),
        FeatureSpec(
            "contact_availability",
            "Contact availability",
            "passthrough",
            "identity",
            "Share of contact channels (phone/website/email) present.",
            "Already a normalized derived score; passed through.",
        ),
        FeatureSpec(
            "category_fit",
            "Category fit",
            "passthrough",
            "identity",
            "Match between business category and configured target categories.",
            "Already a normalized derived score; passed through.",
        ),
        FeatureSpec(
            "location_fit",
            "Location fit",
            "passthrough",
            "identity",
            "Proximity to the configured discovery center (exponential distance decay).",
            "Already a normalized derived score; passed through.",
        ),
        FeatureSpec(
            "website_quality",
            "Website quality",
            "passthrough",
            "identity",
            "Fetched site has a real domain, title, and meaningful content.",
            "Already a normalized derived score; passed through.",
        ),
        FeatureSpec(
            "business_completeness",
            "Business completeness",
            "passthrough",
            "identity",
            "Share of core business fields that carry real values.",
            "Already a normalized derived score; passed through.",
        ),
        FeatureSpec(
            "recent_activity",
            "Recent activity",
            "time_decay",
            "identity",
            "Recency of the last open/close or social event.",
            "Recency fades over time; an exponential decay A(t) = A0·exp(−k·t) "
            "with a configurable half-life models this without a hard cutoff.",
        ),
        FeatureSpec(
            "social_presence",
            "Social presence",
            "passthrough",
            "identity",
            "Any social profile found on the business website.",
            "Already a normalized derived score; passed through.",
        ),
        FeatureSpec(
            "social_activity",
            "Social activity",
            "passthrough",
            "identity",
            "Recency of the latest post across detected profiles.",
            "Already a normalized derived score; passed through.",
        ),
    )
}

# deterministic engine iteration order (config-driven, not dict-luck)
FEATURE_ORDER = [key for key in FEATURE_KEYS if key in FEATURE_SPECS] + [
    key for key in FEATURE_SPECS if key not in FEATURE_KEYS
]


def feature_specs() -> dict[str, FeatureSpec]:
    return dict(FEATURE_SPECS)


def assert_spec_keys_match_config() -> None:
    configured = set(FEATURE_KEYS)
    defined = set(FEATURE_SPECS)
    if configured != defined:
        raise RuntimeError(f"feature/config key mismatch: config={configured} specs={defined}")
