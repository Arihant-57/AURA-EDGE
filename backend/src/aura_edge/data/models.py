"""SQLAlchemy 2.0 ORM models for AURA EDGE (Phase 0 + Phase 1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for all AURA EDGE tables."""


class SystemSetting(Base):
    """Key-value application settings persisted in PostgreSQL."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
        onupdate=_utc_now,
    )


class ConnectivityAuditEvent(Base):
    """Audit trail of outbound connectivity policy decisions."""

    __tablename__ = "connectivity_audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str] = mapped_column(String(512), nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )


class Phase0VectorSmoke(Base):
    """Phase 0 verification table proving pgvector storage and distance queries."""

    __tablename__ = "phase0_vector_smoke"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )


class TimerRecord(Base):
    """Persistent countdown timer stored authoritatively in PostgreSQL."""

    __tablename__ = "timers"
    __table_args__ = (
        CheckConstraint(
            "duration_seconds > 0 AND duration_seconds <= 86400",
            name="ck_timers_duration_range",
        ),
        CheckConstraint(
            "remaining_seconds >= 0 AND remaining_seconds <= duration_seconds",
            name="ck_timers_remaining_range",
        ),
        CheckConstraint(
            "status IN ('active', 'paused', 'completed', 'cancelled', 'missed')",
            name="ck_timers_status_enum",
        ),
        Index("ix_timers_status_fire_at", "status", "fire_at_utc"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    fire_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )
    updated_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
        onupdate=_utc_now,
    )
    completed_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class VoiceTurnMetric(Base):
    """Measured per-turn latency & outcome log for Phase 1+ voice interactions."""

    __tablename__ = "voice_turn_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    turn_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    requested_compute_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    effective_compute_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    intent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_source: Mapped[str] = mapped_column(String(32), nullable=False)
    final_state: Mapped[str] = mapped_column(String(24), nullable=False)
    stt_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    router_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tool_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tts_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    ttfa_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    e2e_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )
