"""ORM models. Entity definitions live in ``ildrs.domain.entities``."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON as GenericJSON

from ildrs.storage.database import Base

JSONType = GenericJSON


def uuid_str() -> str:
    return str(uuid.uuid4())


class BusinessRow(Base):
    __tablename__ = "businesses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    address: Mapped[str] = mapped_column(String(1024), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    website: Mapped[str] = mapped_column(String(1024), default="")
    email: Mapped[str] = mapped_column(String(512), default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    category: Mapped[str] = mapped_column(String(255), default="")
    subcategories: Mapped[list] = mapped_column(JSONType, default=list)
    google_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    business_status: Mapped[str] = mapped_column(String(64), default="")
    collected: Mapped[bool] = mapped_column(Boolean, default=False)
    features: Mapped[dict] = mapped_column(JSONType, default=dict)
    provenance: Mapped[dict] = mapped_column(JSONType, default=dict)
    website_analysis: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    social_links: Mapped[list] = mapped_column(JSONType, default=list)
    recent_activity: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    deduped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    leads: Mapped[list[LeadRow]] = relationship(back_populates="business")

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_business_source_external"),
        Index("ix_businesses_source_status", "source"),
        Index("ix_businesses_duplicate", "is_duplicate"),
    )


class LeadRow(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    business_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("businesses.id", ondelete="CASCADE")
    )
    rating: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    model: Mapped[str] = mapped_column(String(16), default="v1")
    model_version: Mapped[str] = mapped_column(String(64), default="")
    expected_value: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    percentile: Mapped[float | None] = mapped_column(Float, nullable=True)
    features: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    business: Mapped[BusinessRow] = relationship(back_populates="leads")
    outreach: Mapped[list[OutreachRow]] = relationship(back_populates="lead")

    __table_args__ = (
        Index("ix_leads_rating", "rating"),
        Index("ix_leads_status", "status"),
        Index("ix_leads_rank", "rank"),
    )


class OutreachRow(Base):
    __tablename__ = "outreach"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    lead_id: Mapped[str] = mapped_column(String(36), ForeignKey("leads.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(32), default="other")
    status: Mapped[str] = mapped_column(String(32), default="queued")
    note: Mapped[str] = mapped_column(Text, default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    message: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    review_status: Mapped[str] = mapped_column(String(16), default="pending")
    sent_status: Mapped[str] = mapped_column(String(16), default="draft")
    response_status: Mapped[str] = mapped_column(String(16), default="awaiting")
    outcome: Mapped[str] = mapped_column(String(16), default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lead: Mapped[LeadRow] = relationship(back_populates="outreach")

    __table_args__ = (
        Index("ix_outreach_lead", "lead_id"),
        Index("ix_outreach_review", "review_status"),
        Index("ix_outreach_sent", "sent_status"),
    )


class OutreachMonitorRow(Base):
    """Health row for one response-monitoring channel.

    Persisted so the dashboard always shows LAST CHECKED / NEXT CHECK / STATUS
    even across restarts, and so "not configured" is visible without any job
    having run.
    """

    __tablename__ = "outreach_monitors"

    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    configured: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="unavailable")
    detail: Mapped[str] = mapped_column(Text, default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HistoricalOutcomeRow(Base):
    __tablename__ = "historical_outcomes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    business_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("businesses.id", ondelete="CASCADE")
    )
    lead_id: Mapped[str] = mapped_column(String(36), ForeignKey("leads.id", ondelete="CASCADE"))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    features: Mapped[dict] = mapped_column(JSONType, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_historical_outcomes_outcome", "outcome"),
        Index("ix_historical_outcomes_business", "business_id"),
    )


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    counts: Mapped[dict] = mapped_column(JSONType, default=dict)
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_jobs_stage_status", "stage", "status"),)


class NotificationRow(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    level: Mapped[str] = mapped_column(String(16), default="info")
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_notifications_created", "created_at"),)


class AppMetaRow(Base):
    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
