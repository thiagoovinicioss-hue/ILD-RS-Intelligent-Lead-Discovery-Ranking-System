"""Tests for duplicate detection (normalization.deduplicator) and pipeline hooks."""

from __future__ import annotations

import pytest

from ildrs.domain.entities import Business
from ildrs.normalization.deduplicator import (
    duplicate_pair,
    find_duplicate_clusters,
    pick_canonical,
    summarize,
)


def business(**overrides) -> Business:
    base = {
        "source": "fixture",
        "external_id": "fix-1",
        "name": "Apex Plumbing Co",
        "category": "plumber",
        "phone": "+1 (512) 555-0100",
        "website": "https://apex.example.com",
        "review_count": 10,
    }
    base.update(overrides)
    return Business(**base)


class TestDuplicatePair:
    def test_same_phone_is_duplicate(self):
        a = business(external_id="fix-1", phone="512-555-0100")
        b = business(external_id="fix-2", phone="+15125550100")
        assert duplicate_pair(a, b)

    def test_same_name_and_category_is_duplicate(self):
        a = business(external_id="fix-1", phone="", website="")
        b = business(external_id="fix-2", name="apex plumbing co", phone="", website="")
        assert duplicate_pair(a, b)

    def test_same_domain_but_different_name_is_not_duplicate(self):
        a = business(
            external_id="fix-1",
            name="Apex Plumbing",
            phone="512-555-0100",
            website="https://apex.example.com",
        )
        b = business(
            external_id="fix-2",
            name="Apex Roofing",
            phone="512-555-0200",
            website="https://apex.example.com",
        )
        assert not duplicate_pair(a, b)

    def test_same_name_different_category_is_not_duplicate(self):
        a = business(external_id="fix-1", name="Apex Co", category="plumber", phone="", website="")
        b = business(external_id="fix-2", name="Apex Co", category="bakery", phone="", website="")
        assert not duplicate_pair(a, b)


class TestClustering:
    def test_clusters_by_phone(self):
        items = [
            business(external_id="a", phone="512-555-0100"),
            business(external_id="b", phone="+15125550100"),
            business(
                external_id="c",
                phone="512-555-9999",
                category="bakery",
                website="https://other.example.com",
            ),
        ]
        clusters = find_duplicate_clusters(items)
        assert len(clusters) == 1
        assert {items[i].external_id for i in clusters[0]} == {"a", "b"}

    def test_pick_canonical_prefers_review_count(self):
        items = [
            business(external_id="a", phone="512-555-0100", review_count=5),
            business(external_id="b", phone="+15125550100", review_count=120),
        ]
        cluster = find_duplicate_clusters(items)[0]
        canonical = pick_canonical(cluster, items)
        assert items[canonical].external_id == "b"

    def test_summarize_reports_duplicate_ids(self):
        items = [
            business(external_id="a", phone="512-555-0100", review_count=5),
            business(external_id="b", phone="+15125550100", review_count=120),
        ]
        clusters, count = summarize(items, (b.external_id for b in items))
        assert count == 1
        assert len(clusters) == 1
        assert clusters[0].canonical_id == "b"
        assert clusters[0].duplicate_ids == ["a"]


class TestPipelineDedup:
    @pytest.mark.asyncio
    async def test_discover_stage_skips_existing_duplicate(self, db):
        from ildrs.notifications.notifier import Notifier
        from ildrs.pipeline.stages import discover_stage
        from ildrs.sources.base import DiscoveryQuery

        source = _SingleCandidateSource()
        query = DiscoveryQuery(query="plumber", limit=5)
        notifier = Notifier(db)

        first = await discover_stage(db, source, notifier, query=query, limit=5)
        assert first["discovered"] == 1
        assert first["duplicates_skipped"] == 0

        # same name + category, different external id → should be skipped as duplicate
        second = await discover_stage(db, source, notifier, query=query, limit=5)
        assert second["discovered"] == 0
        assert second["duplicates_skipped"] == 1

        # without dedupe the candidate is inserted again
        third = await discover_stage(db, source, notifier, query=query, limit=5, dedupe=False)
        assert third["discovered"] == 1
        assert third["duplicates_skipped"] == 0


class _SingleCandidateSource:
    name = "fixture"
    calls = 0

    async def discover(self, query):
        self.calls += 1
        return [_candidate(external_id=f"new-id-{self.calls}")]


def _candidate(external_id: str):
    from ildrs.domain.entities import Candidate

    return Candidate(
        source="fixture",
        external_id=external_id,
        name="Apex Plumbing Co",
        category="plumber",
    )
