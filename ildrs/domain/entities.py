"""Core domain entities.

These are plain, dependency-free objects used by the pipeline. Persistence
mapping happens in ``ildrs.storage.models``; API serialization happens in
the routes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ildrs.domain.provenance import ProvenanceMap, utcnow

STAGE_NAMES = ("discover", "collect", "analyze", "rate", "rank", "verify")

JOB_STATUSES = ("pending", "running", "completed", "failed", "cancelled")

LEAD_STATUSES = ("new", "reviewed", "outreach", "contacted", "won", "lost", "dismissed")

OUTREACH_STATUSES = (
    "queued",
    "sent",
    "no_response",
    "responded",
    "interested",
    "declined",
    "converted",
)

OUTREACH_CHANNELS = ("email", "phone", "linkedin", "other")

OUTCOME_POSITIVE = {"responded", "interested", "converted"}

# Review-queue lifecycle for a proposed outreach message.
REVIEW_STATUSES = ("pending", "approved", "rejected", "edited")

# Delivery lifecycle, kept separate from review so a rejected draft is never sent.
OUTREACH_SENT_STATUSES = ("draft", "queued", "sent", "failed")

# Response monitoring lifecycle for a sent outreach item.
RESPONSE_STATUSES = (
    "awaiting",
    "no_response",
    "responded",
    "interested",
    "declined",
    "converted",
)

# Monitor channel status reported by the response-monitoring scheduler.
MONITOR_STATUSES = ("operational", "checking", "unavailable", "error")


@dataclass
class Candidate:
    """A raw discovery result before details are collected."""

    source: str
    external_id: str
    name: str
    address: str = ""
    latitude: float | None = None
    longitude: float | None = None
    category: str = ""
    subcategories: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Business:
    """A discovered business with collected data + provenance."""

    source: str
    external_id: str | None
    name: str
    address: str = ""
    phone: str = ""
    website: str = ""
    email: str = ""
    latitude: float | None = None
    longitude: float | None = None
    category: str = ""
    subcategories: list[str] = field(default_factory=list)
    google_rating: float | None = None
    review_count: int = 0
    business_status: str = ""
    # enrichment data — stored raw, separate from derived features
    website_analysis: dict | None = None
    social_links: list[str] = field(default_factory=list)
    recent_activity: datetime | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    last_verified_at: datetime | None = None
    provenance: ProvenanceMap = field(default_factory=ProvenanceMap)

    def completeness_fields(self) -> dict[str, bool]:
        """Which core fields carry real values (for the completeness feature)."""
        return {
            "name": bool(self.name),
            "address": bool(self.address),
            "category": bool(self.category),
            "website": bool(self.website),
            "phone": bool(self.phone),
            "email": bool(self.email),
            "rating": self.google_rating is not None,
            "status": bool(self.business_status),
        }


@dataclass
class FeatureValue:
    key: str
    value: float
    weight: float
    provenance_kind: str
    raw_value: Any = None

    @property
    def contribution(self) -> float:
        return self.value * self.weight

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": round(self.value, 4),
            "weight": round(self.weight, 4),
            "contribution": round(self.contribution, 4),
            "provenance": self.provenance_kind,
            "raw_value": self.raw_value,
        }


@dataclass
class FeatureVector:
    business_id: str
    features: dict[str, FeatureValue] = field(default_factory=dict)

    def keys(self) -> list[str]:
        return list(self.features.keys())

    def to_dict(self) -> dict[str, Any]:
        return {k: v.to_dict() for k, v in self.features.items()}


@dataclass
class RatingResult:
    rating: float  # 0..100
    confidence: float  # 0..1
    model: str
    model_version: str
    breakdown: dict[str, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rating": round(self.rating, 2),
            "confidence": round(self.confidence, 4),
            "model": self.model,
            "model_version": self.model_version,
            "breakdown": self.breakdown,
            "metadata": self.metadata,
        }


@dataclass
class Job:
    id: str
    stage: str
    status: str = "pending"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutreachRecord:
    id: str
    lead_id: str
    channel: str
    status: str
    note: str = ""
    occurred_at: datetime = field(default_factory=utcnow)
    message: str = ""
    reason: str = ""
    created_at: datetime = field(default_factory=utcnow)
    review_status: str = "pending"
    sent_status: str = "draft"
    response_status: str = "awaiting"
    outcome: str = ""
    sent_at: datetime | None = None
    last_checked_at: datetime | None = None
    next_check_at: datetime | None = None


@dataclass
class MessageDraft:
    """A proposed outreach message generated from verified business data.

    ``facts`` lists only the observed facts referenced by the message (each
    tagged with its provenance). ``suggestions`` are clearly-labeled generated
    content — the reader can always tell what is real from what is suggested.
    """

    message: str
    reason: str
    facts: list[dict[str, object]] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


@dataclass
class ResponseMonitorStatus:
    """Health of one response-monitoring channel."""

    source: str
    configured: bool
    status: str = "unavailable"
    detail: str = ""
    last_checked_at: datetime | None = None
    next_check_at: datetime | None = None


@dataclass
class Notification:
    id: str
    level: str
    title: str
    body: str = ""
    read: bool = False
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class HistoricalOutcome:
    id: str
    business_id: str
    lead_id: str
    outcome: str
    outcome_value: int
    features: dict[str, Any] = field(default_factory=dict)
    recorded_at: datetime = field(default_factory=utcnow)
