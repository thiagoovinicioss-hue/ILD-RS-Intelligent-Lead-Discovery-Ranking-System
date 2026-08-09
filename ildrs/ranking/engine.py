"""Lead ranking.

Pure, deterministic ranking of leads by rating (descending) with a
transparent tie-breaking rule, dense rank assignment, and percentile.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RankedLead:
    lead_id: str
    rating: float
    confidence: float = 0.0
    created_at: str = ""


class RankingEngine:
    def rank(self, leads: list[RankedLead]) -> list[RankedLead]:
        """Return leads sorted by (rating desc, confidence desc, oldest first)
        and annotate each with dense rank + percentile in [0, 1]."""
        ordered = sorted(
            leads,
            key=lambda lead: (-lead.rating, -lead.confidence, lead.created_at),
        )

        # dense ranking: equal ratings share the same rank
        dense: list[tuple[RankedLead, int, float]] = []
        current_rank = 0
        prev_rating: float | None = None

        for lead in ordered:
            if prev_rating is None or lead.rating != prev_rating:
                current_rank += 1
            percentile = _percentile(lead.rating, ordered)
            dense.append((lead, current_rank, percentile))
            prev_rating = lead.rating

        return [
            RankedLead(
                lead_id=lead.lead_id,
                rating=lead.rating,
                confidence=lead.confidence,
                created_at=lead.created_at,
            )
            for lead, _rank, _pct in dense
        ]

    def dense_ranks(self, leads: list[RankedLead]) -> list[tuple[str, int, float]]:
        """(lead_id, dense_rank, percentile) tuples, best first."""
        ordered = sorted(
            leads,
            key=lambda lead: (-lead.rating, -lead.confidence, lead.created_at),
        )
        result: list[tuple[str, int, float]] = []
        current_rank = 0
        prev_rating: float | None = None
        for lead in ordered:
            if prev_rating is None or lead.rating != prev_rating:
                current_rank += 1
            result.append((lead.lead_id, current_rank, _percentile(lead.rating, ordered)))
            prev_rating = lead.rating
        return result


def _percentile(rating: float, ordered: list[RankedLead]) -> float:
    """Fraction of leads with a rating strictly below this one."""
    if not ordered:
        return 0.0
    below = sum(1 for lead in ordered if lead.rating < rating)
    return below / len(ordered)
