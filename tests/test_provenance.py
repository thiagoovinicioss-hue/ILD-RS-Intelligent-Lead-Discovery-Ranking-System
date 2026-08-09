"""Tests for the provenance model (Architecture §3.3)."""

from __future__ import annotations

from ildrs.domain.provenance import DataSourceKind, ProvenanceMap
from tests.conftest import make_business


def test_kind_semantics():
    assert DataSourceKind.DIRECT.value == "direct"
    assert DataSourceKind.UNAVAILABLE.value == "unavailable"


def test_set_and_get():
    p = ProvenanceMap()
    p.set("rating", DataSourceKind.DIRECT, "fixture", 4.8)
    entry = p.get("rating")
    assert entry is not None
    assert entry.kind is DataSourceKind.DIRECT
    assert entry.provider == "fixture"
    assert entry.raw_value == 4.8


def test_kind_of():
    p = ProvenanceMap()
    p.set("rating", DataSourceKind.DERIVED, "pipeline", 0.9)
    assert p.kind_of("rating") is DataSourceKind.DERIVED
    assert p.kind_of("missing") is None


def test_is_available():
    p = ProvenanceMap()
    p.set("rating", DataSourceKind.DIRECT, "fixture", 4.0)
    p.set("email", DataSourceKind.DERIVED, "fixture", "a@b.co")
    p.set("phone", DataSourceKind.UNAVAILABLE, "fixture", None)
    assert p.is_available("rating")
    assert p.is_available("email")
    assert not p.is_available("phone")
    assert not p.is_available("missing")


def test_missing_field_is_absent():
    p = ProvenanceMap()
    assert p.get("missing") is None
    assert not p.is_available("missing")


def test_roundtrip_dict():
    p = ProvenanceMap()
    p.set("rating", DataSourceKind.DIRECT, "fixture", 4.8)
    restored = ProvenanceMap.from_dict(p.to_dict())
    entry = restored.get("rating")
    assert entry is not None
    assert entry.kind is DataSourceKind.DIRECT
    assert entry.provider == "fixture"
    assert entry.raw_value == 4.8


def test_from_dict_empty():
    assert len(ProvenanceMap.from_dict(None)) == 0
    assert len(ProvenanceMap.from_dict({})) == 0


def test_business_avoids_fabrication():
    """Unavailable phone data must never be invented: value stays empty."""
    b = make_business(has_phone=False)
    assert b.phone == ""
    assert b.provenance.kind_of("phone") is DataSourceKind.UNAVAILABLE


def test_business_tracks_available_data():
    b = make_business(has_phone=True, has_website=True)
    assert b.provenance.is_available("phone")
    assert b.provenance.is_available("website")
    assert b.provenance.is_available("rating")
