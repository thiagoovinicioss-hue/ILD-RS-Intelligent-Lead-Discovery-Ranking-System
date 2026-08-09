"""Rating explanations.

No rating is ever emitted without an explanation. Each contribution is
rendered as a human-readable line in rating points (0–100 scale, additive to
the total)::

    Website presence: +18.0
    Recent activity: +14.2
    Contact availability: no data (excluded)

``build_explanation`` produces the full ordered list with a total line, so the
system can always answer *"why is this lead rated N?"*.
"""

from __future__ import annotations

from typing import Any

from ildrs.rating.spec import FEATURE_SPECS


def format_contribution(label: str, contribution: float) -> str:
    """'+18.0', '-3.5' or '0.0' for a contribution in rating points."""
    return f"{label}: {contribution:+.1f}"


def explain_feature(key: str, entry: dict[str, Any]) -> str:
    """Render one breakdown entry as an explanation line."""
    label = FEATURE_SPECS[key].label if key in FEATURE_SPECS else key
    contribution = entry.get("contribution")
    if contribution is None or entry.get("provenance") == "unavailable":
        return f"{label}: no data (excluded)"
    return format_contribution(label, float(contribution))


def build_explanation(breakdown: dict[str, dict[str, Any]], rating: float) -> list[str]:
    """Ordered explanation lines: unavailable first, then by magnitude."""
    unavailable = [
        explain_feature(key, entry)
        for key, entry in breakdown.items()
        if entry.get("provenance") == "unavailable"
    ]
    with_data = [
        explain_feature(key, entry)
        for key, entry in breakdown.items()
        if entry.get("provenance") != "unavailable"
    ]
    with_data.sort(key=lambda line: -abs(_contribution_of(line)))
    return unavailable + with_data + [f"Total rating: {rating:.1f} / 100"]


def _contribution_of(line: str) -> float:
    """Parse the signed contribution out of an explanation line."""
    tail = line.split(":", 1)[-1].strip()
    try:
        return float(tail)
    except ValueError:
        return 0.0
