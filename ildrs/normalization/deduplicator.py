"""Duplicate detection for businesses and candidates.

Matching is conservative: two records are considered duplicates only when a
high-precision identity key agrees (phone, or website domain) or when the
normalized name AND category agree. Everything else is left alone — false
negatives are acceptable, false positives are not.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from ildrs.normalization.normalizers import (
    normalize_category,
    normalize_name,
    normalize_phone,
    website_domain,
)


def _name(b: object) -> str:
    return normalize_name(getattr(b, "name", ""))


def _category(b: object) -> str:
    return normalize_category(getattr(b, "category", ""))


def _phone(b: object) -> str:
    return normalize_phone(getattr(b, "phone", ""))


def _domain(b: object) -> str:
    return website_domain(getattr(b, "website", ""))


def duplicate_pair(a: object, b: object) -> bool:
    """True when two records should be treated as the same business."""
    if a is b:
        return False
    phone_a, phone_b = _phone(a), _phone(b)
    if phone_a and phone_a == phone_b:
        return True
    domain_a, domain_b = _domain(a), _domain(b)
    if domain_a and domain_a == domain_b and _name(a) == _name(b):
        return True
    if _name(a) and _name(a) == _name(b) and _category(a) and _category(a) == _category(b):
        return True
    return False


def _union_find(n: int, pairs: Iterable[tuple[int, int]]) -> list[set[int]]:
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for x, y in pairs:
        union(x, y)
    groups: dict[int, set[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), set()).add(i)
    return list(groups.values())


def find_duplicate_clusters(businesses: Sequence[object]) -> list[set[int]]:
    """Group indices of ``businesses`` that represent the same entity.

    Only clusters of size >= 2 are returned.
    """
    n = len(businesses)
    buckets: dict[str, list[int]] = {}
    for i, b in enumerate(businesses):
        for key in (_name(b), _phone(b), _domain(b)):
            if key:
                buckets.setdefault(key, []).append(i)

    pairs: set[tuple[int, int]] = set()
    for members in buckets.values():
        for i, left in enumerate(members):
            for right in members[i + 1 :]:
                if left != right and duplicate_pair(businesses[left], businesses[right]):
                    pairs.add((min(left, right), max(left, right)))

    return [g for g in _union_find(n, pairs) if len(g) >= 2]


def pick_canonical(group: Iterable[int], businesses: Sequence[object]) -> int:
    """Most authoritative member of a cluster (highest review_count, newest)."""
    ranked = sorted(
        group,
        key=lambda i: (
            int(getattr(businesses[i], "review_count", 0) or 0),
            str(getattr(businesses[i], "created_at", "") or ""),
        ),
        reverse=True,
    )
    return ranked[0]


@dataclass
class DuplicateCluster:
    cluster_id: int
    member_ids: list[str]
    canonical_id: str | None = None

    @property
    def duplicate_ids(self) -> list[str]:
        if not self.canonical_id:
            return list(self.member_ids)
        return [m for m in self.member_ids if m != self.canonical_id]


def summarize(
    businesses: Sequence[object],
    id_of: Iterator[str],
) -> tuple[list[DuplicateCluster], int]:
    """Summarize clusters by business id. Returns (clusters, total_duplicates)."""
    ids = list(id_of)
    if len(ids) != len(businesses):
        raise ValueError("id_of must yield one id per business")
    clusters: list[DuplicateCluster] = []
    duplicate_count = 0
    for cluster_id, group in enumerate(find_duplicate_clusters(businesses), start=1):
        member_ids = [ids[i] for i in sorted(group)]
        canonical_idx = pick_canonical(group, businesses)
        cluster = DuplicateCluster(
            cluster_id=cluster_id,
            member_ids=member_ids,
            canonical_id=ids[canonical_idx],
        )
        clusters.append(cluster)
        duplicate_count += len(cluster.duplicate_ids)
    return clusters, duplicate_count
