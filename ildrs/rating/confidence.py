"""Confidence score (separate from the rating).

Rating and confidence answer different questions:

- **rating** — how attractive the lead looks, given the data we have.
- **confidence** — how much we trust that rating, given how much of the data
  is actually present.

A business can legitimately have a high rating but low confidence (only a few
high-weight features were observed and everything else is missing).

Formula (documented): confidence is the weighted share of configured features
whose provenance is *available* (any kind except ``unavailable``):

    confidence = Σᵢ wᵢ · knownᵢ        knownᵢ = 1 if feature i has data else 0

This is deterministic and uses the model's own normalized weights, not whatever
weights happen to be attached to an incoming vector.
"""

from __future__ import annotations

from ildrs.domain.entities import FeatureValue

KNOWN_PROVENANCE = ("direct", "derived", "inferred")


def confidence_from_features(features: dict[str, FeatureValue], weights: dict[str, float]) -> float:
    """Weighted data-availability confidence in [0, 1]."""
    total = 0.0
    available = 0.0
    for key, weight in weights.items():
        total += weight
        fv = features.get(key)
        if fv is not None and fv.provenance_kind in KNOWN_PROVENANCE:
            available += weight
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, available / total))


def confidence_label(confidence: float) -> str:
    """Human label for a confidence value."""
    if confidence >= 0.8:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"
