"""Provenance metadata.

Every important business field must be traceable to where it came from.
Four kinds are allowed; the system must never fabricate data:

- ``direct``      returned as-is by a provider
- ``derived``     computed from provider data by our pipeline
- ``inferred``    filled by a documented heuristic from other fields
- ``unavailable`` the provider does not supply this data
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

PROVENANCE_KINDS = ("direct", "derived", "inferred", "unavailable")


class DataSourceKind(StrEnum):
    DIRECT = "direct"
    DERIVED = "derived"
    INFERRED = "inferred"
    UNAVAILABLE = "unavailable"


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Provenance:
    """Provenance of a single business field."""

    kind: DataSourceKind
    provider: str = ""  # name of the provider/step that supplied it
    raw_value: Any = None
    captured_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.captured_at is None:
            self.captured_at = utcnow()
        if not isinstance(self.kind, DataSourceKind):
            self.kind = DataSourceKind(self.kind)

    @property
    def is_available(self) -> bool:
        return self.kind is not DataSourceKind.UNAVAILABLE

    def to_dict(self) -> dict[str, Any]:
        raw = self.raw_value
        return {
            "kind": self.kind.value,
            "provider": self.provider,
            "raw_value": raw,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Provenance | None:
        if not data:
            return None
        captured = data.get("captured_at")
        try:
            captured_dt = datetime.fromisoformat(captured) if captured else None
        except ValueError:
            captured_dt = None
        return cls(
            kind=DataSourceKind(data.get("kind", "unavailable")),
            provider=data.get("provider", ""),
            raw_value=data.get("raw_value"),
            captured_at=captured_dt,
        )


@dataclass
class ProvenanceMap:
    """Per-field provenance store for a business."""

    items: dict[str, Provenance] = field(default_factory=dict)

    def set(
        self, field: str, kind: DataSourceKind, provider: str = "", raw_value: Any = None
    ) -> None:
        self.items[field] = Provenance(kind=kind, provider=provider, raw_value=raw_value)

    def get(self, field: str) -> Provenance | None:
        return self.items.get(field)

    def kind_of(self, field: str) -> DataSourceKind | None:
        prov = self.items.get(field)
        return prov.kind if prov else None

    def is_available(self, field: str) -> bool:
        prov = self.items.get(field)
        return bool(prov and prov.is_available)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {k: v.to_dict() for k, v in self.items.items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ProvenanceMap:
        out = cls()
        if not data:
            return out
        for k, v in data.items():
            prov = Provenance.from_dict(v)
            if prov:
                out.items[k] = prov
        return out

    def __len__(self) -> int:
        return len(self.items)
