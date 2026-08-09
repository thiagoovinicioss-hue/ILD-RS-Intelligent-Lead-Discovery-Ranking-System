"""Tests for lead ranking (Architecture §6)."""

from __future__ import annotations

import pytest

from ildrs.ranking.engine import RankedLead, RankingEngine, _percentile


def _lead(lead_id: str, rating: float, confidence: float = 0.0, created_at: str = "") -> RankedLead:
    return RankedLead(lead_id=lead_id, rating=rating, confidence=confidence, created_at=created_at)


def test_sorted_by_rating_descending():
    engine = RankingEngine()
    leads = [_lead("a", 40.0), _lead("b", 90.0), _lead("c", 60.0)]
    ranked = engine.rank(leads)
    assert [lead.lead_id for lead in ranked] == ["b", "c", "a"]


def test_tie_breaks_by_confidence():
    engine = RankingEngine()
    leads = [_lead("low", 80.0, confidence=0.2), _lead("high", 80.0, confidence=0.9)]
    ranked = engine.rank(leads)
    assert [lead.lead_id for lead in ranked] == ["high", "low"]


def test_dense_rank_shares_ranks():
    engine = RankingEngine()
    leads = [_lead("a", 90.0), _lead("b", 90.0), _lead("c", 70.0)]
    ranks = engine.dense_ranks(leads)
    ranks_by_id = {lead_id: rank for lead_id, rank, _ in ranks}
    assert ranks_by_id["a"] == 1
    assert ranks_by_id["b"] == 1
    assert ranks_by_id["c"] == 2


def test_percentile_mapping():
    engine = RankingEngine()
    leads = [_lead("a", 90.0), _lead("b", 90.0), _lead("c", 70.0)]
    ranks = engine.dense_ranks(leads)
    pct = {lead_id: p for lead_id, _rank, p in ranks}
    # percentile = share of leads strictly below the rating
    assert pct["a"] == pytest.approx(1 / 3)
    assert pct["c"] == 0.0


def test_rank_preserves_lead_attributes():
    engine = RankingEngine()
    ranked = engine.rank([_lead("a", 55.5, confidence=0.4, created_at="2026-01-01")])
    assert ranked[0].rating == 55.5
    assert ranked[0].confidence == 0.4
    assert ranked[0].created_at == "2026-01-01"


def test_empty_input():
    engine = RankingEngine()
    assert engine.rank([]) == []
    assert engine.dense_ranks([]) == []


def test_percentile_below_count():
    leads = [_lead("a", 90.0), _lead("b", 70.0), _lead("c", 50.0)]
    assert _percentile(70.0, leads) == pytest.approx(1 / 3)
