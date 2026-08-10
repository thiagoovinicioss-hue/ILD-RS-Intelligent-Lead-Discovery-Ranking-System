"""Feature normalization.

Raw feature values are heterogeneous: booleans, bounded scores, counts, and
timestamps. They are never summed as-is. Each raw value is first mapped onto a
common [0, 1] scale through a documented normalizer, exactly as declared in
:mod:`ildrs.rating.spec`.

Missing/raw-fallbacks: when a feature carries no usable raw value (or the raw
value is an opaque derived object), the engine falls back to the already
normalized value stored on the :class:`FeatureValue` (the pipeline's extractor
produces those). The engine never fabricates data.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from ildrs.domain.entities import FeatureValue
from ildrs.rating.decay import (
    days_between,
    decay_rate_from_half_life,
    exponential_decay,
    parse_timestamp,
)
from ildrs.rating.spec import (
    FEATURE_SPECS,
    RATING_HI,
    RATING_LO,
    REVIEW_CAP,
    STATUS_MAPPING,
)

STATUS_DEFAULT = 0.2


def clamp01(value: float) -> float:
    """Clamp to [0, 1]."""
    return max(0.0, min(1.0, float(value)))


def normalize_binary(raw: Any) -> float:
    """Presence ⇒ 1.0, absence ⇒ 0.0."""
    return 1.0 if raw else 0.0


def normalize_score01(raw: Any, *, lo: float = RATING_LO, hi: float = RATING_HI) -> float:
    """Linear mapping of a bounded score onto [0, 1], clamped."""
    if raw is None:
        return 0.0
    value = float(raw)
    span = hi - lo
    if span <= 0:
        return 0.0
    return clamp01((value - lo) / span)


def normalize_count_log(raw: Any, *, cap: float = REVIEW_CAP) -> float:
    """log10(count + 1) / log10(cap + 1), saturating at the cap."""
    if raw is None:
        return 0.0
    count = float(raw)
    if count <= 0:
        return 0.0
    return clamp01(math.log10(count + 1.0) / math.log10(cap + 1.0))


def normalize_categorical(raw: Any, mapping: dict[str, float], default: float) -> float:
    """Discrete value mapped through a documented table, else ``default``."""
    if raw is None:
        return default
    key = str(raw).upper()
    return float(mapping.get(key, default))


def normalize_passthrough(value: float) -> float:
    """A derived score that the pipeline already normalized."""
    return clamp01(value)


def normalize_time_decay(
    raw: Any,
    *,
    now: datetime,
    half_life_days: float,
    fallback_value: float,
) -> float:
    """Exponential recency decay A(t) = A0·exp(−k·t) from a timestamp.

    ``raw`` may be an ISO timestamp (preferred), a ``datetime``, or absent.
    When absent, the stored normalized value is used as the fallback signal.
    """
    if raw is None or raw == "":
        return clamp01(fallback_value)
    parsed = parse_timestamp(raw)
    if parsed is None:
        return clamp01(fallback_value)
    t = days_between(parsed, now)
    k = decay_rate_from_half_life(half_life_days)
    return clamp01(exponential_decay(t, k, a0=1.0))


def normalize_feature(key: str, fv: FeatureValue, *, now: datetime, half_life_days: float) -> float:
    """Normalize one feature to [0, 1] using its declared spec."""
    spec = FEATURE_SPECS.get(key)
    if spec is None:
        return clamp01(fv.value)

    kind = spec.normalize
    raw = fv.raw_value

    if kind == "passthrough":
        return normalize_passthrough(fv.value)

    # A raw value that is an opaque derived object carries no usable primitive.
    if isinstance(raw, (list, tuple, dict, set)):
        return normalize_passthrough(fv.value)

    if raw is None or raw == "":
        return normalize_passthrough(fv.value)

    if kind == "binary":
        return normalize_binary(raw)
    if kind == "score01":
        return normalize_score01(raw)
    if kind == "count_log":
        return normalize_count_log(raw)
    if kind == "categorical":
        return normalize_categorical(raw, STATUS_MAPPING, STATUS_DEFAULT)
    if kind == "time_decay":
        return normalize_time_decay(
            raw, now=now, half_life_days=half_life_days, fallback_value=fv.value
        )
    return normalize_passthrough(fv.value)
