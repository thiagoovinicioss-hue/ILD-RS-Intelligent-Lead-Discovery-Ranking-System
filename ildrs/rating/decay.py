"""Exponential time decay for recency signals.

Model:

    A(t) = A0 · exp(−k·t)

where

- ``t``  = time elapsed since the activity (in days, t ≥ 0)
- ``A0`` = initial magnitude of the signal (default 1.0)
- ``k``  = decay coefficient, units 1/day, k = ln(2) / t½

The decay coefficient is not an arbitrary constant: it is derived from a
documented half-life ``t½`` (the time after which the signal is worth half of
its initial value). ``t½`` is configurable via ``ILD_RATING_DECAY_HALF_LIFE_DAYS``
and treated as a hypothesis, not a truth.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any


def decay_rate_from_half_life(half_life_days: float) -> float:
    """Decay coefficient k = ln(2) / t½ given a half-life in days."""
    if half_life_days is None or half_life_days <= 0:
        raise ValueError(f"half-life must be positive days, got {half_life_days!r}")
    return math.log(2.0) / half_life_days


def half_life(decay_rate: float) -> float:
    """Half-life t½ = ln(2) / k given a decay coefficient in 1/day."""
    if decay_rate is None or decay_rate <= 0:
        raise ValueError(f"decay rate must be positive 1/day, got {decay_rate!r}")
    return math.log(2.0) / decay_rate


def exponential_decay(t: float, k: float, a0: float = 1.0) -> float:
    """A(t) = A0 · exp(−k·t), clamped to [0, A0].

    Negative elapsed time or decay rate is a data problem, never an excuse to
    grow the signal, so both are clamped to 0 (A(0) = A0).
    """
    if a0 < 0:
        raise ValueError(f"initial magnitude must be >= 0, got {a0!r}")
    t = max(0.0, float(t))
    k = max(0.0, float(k))
    return a0 * math.exp(-k * t)


def days_between(earlier: datetime, later: datetime) -> float:
    """Elapsed time in (possibly fractional) days between two timestamps."""
    delta = later - earlier
    return max(0.0, delta.total_seconds() / 86400.0)


def parse_timestamp(value: Any) -> datetime | None:
    """Parse a datetime, ISO string, or naive datetime into an aware datetime."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def time_decay_signal(
    timestamp: datetime | str | None,
    *,
    now: datetime | None = None,
    half_life_days: float,
    a0: float = 1.0,
) -> float:
    """Recency signal A(t) = A0·exp(−k·t) from an activity timestamp.

    A ``None`` or unparseable timestamp produces no signal (0.0): the engine
    never fabricates recency data.
    """
    parsed = parse_timestamp(timestamp)
    if parsed is None:
        return 0.0
    now = now or datetime.now(UTC)
    t = days_between(parsed, now)
    k = decay_rate_from_half_life(half_life_days)
    return exponential_decay(t, k, a0=a0)
