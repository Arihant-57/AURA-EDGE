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
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
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


class SmartHomeDevice(Base):
    """Persistent simulated device and its authoritative current state."""

    __tablename__ = "smart_home_devices"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    room: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    device_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    aliases: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="simulated")
    is_essential: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    power_rating_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, server_default=func.now()
    )


class SmartHomeStateHistory(Base):
    """Append-only audit log for every verified simulated device transition."""

    __tablename__ = "smart_home_state_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    old_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    new_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    context_id: Mapped[str] = mapped_column(String(64), nullable=False)
    context_source: Mapped[str] = mapped_column(String(24), nullable=False)
    recorded_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, server_default=func.now()
    )


class SmartHomeRoutine(Base):
    """Persistent deterministic routine definition (built-in + custom)."""

    __tablename__ = "smart_home_routines"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    aliases: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    triggers: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=lambda: [{"type": "manual"}]
    )
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    last_run_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class MemorySetting(Base):
    """Per-profile settings controlling long-term memory and history persistence."""

    __tablename__ = "memory_settings"
    __table_args__ = (
        CheckConstraint(
            "retention_days IS NULL OR retention_days > 0",
            name="ck_memory_settings_retention_positive",
        ),
    )

    profile_id: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    memory_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_save_preferences: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    persist_conversation_history: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    semantic_search_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    embedding_model: Mapped[str] = mapped_column(
        String(64), nullable=False, default="nomic-embed-text:latest"
    )
    updated_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
        onupdate=_utc_now,
    )


class MemoryItem(Base):
    """Persistent personal memory / structured preference item stored in PostgreSQL + pgvector."""

    __tablename__ = "memory_items"
    __table_args__ = (
        CheckConstraint(
            "category IN ('appliance_preference', 'preference', 'fact', 'instruction', 'profile')",
            name="ck_memory_items_category_enum",
        ),
        CheckConstraint(
            "language IN ('en', 'hi', 'kn')",
            name="ck_memory_items_language_enum",
        ),
        CheckConstraint(
            "provenance IN ('explicit_voice', 'explicit_text', 'ui_edit', 'api', 'imported')",
            name="ck_memory_items_provenance_enum",
        ),
        Index(
            "uq_memory_items_profile_canonical_key",
            "profile_id",
            "canonical_key",
            unique=True,
            postgresql_where=text("canonical_key IS NOT NULL"),
        ),
        Index(
            "ix_memory_items_profile_cat_enabled",
            "profile_id",
            "category",
            "enabled",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="preference")
    canonical_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    structured_value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    provenance: Mapped[str] = mapped_column(String(32), nullable=False, default="explicit_text")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
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


class ConversationHistoryItem(Base):
    """Optional opt-in persistent conversation history separate from durable preferences."""

    __tablename__ = "conversation_history_items"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_conv_history_role_enum"),
        CheckConstraint("language IN ('en', 'hi', 'kn')", name="ck_conv_history_lang_enum"),
        Index("ix_conv_history_profile_created", "profile_id", "created_at_utc"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    intent: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )


class ScheduledJob(Base):
    """PostgreSQL-backed persistent scheduler job (reminders, device actions, routines)."""

    __tablename__ = "scheduled_jobs"
    __table_args__ = (
        CheckConstraint(
            "job_type IN ('reminder', 'device_action', 'routine')",
            name="ck_scheduled_jobs_type_enum",
        ),
        CheckConstraint(
            "language IN ('en', 'hi', 'kn')",
            name="ck_scheduled_jobs_lang_enum",
        ),
        CheckConstraint(
            "status IN ('scheduled', 'due', 'running', 'completed', 'failed', 'missed', 'cancelled', 'paused')",
            name="ck_scheduled_jobs_status_enum",
        ),
        CheckConstraint(
            "missed_policy IN ('notify_missed', 'skip', 'execute_if_safe')",
            name="ck_scheduled_jobs_missed_policy_enum",
        ),
        Index("ix_scheduled_jobs_due", "enabled", "status", "next_run_at_utc"),
        Index("ix_scheduled_jobs_type_status", "job_type", "status"),
        Index("ix_scheduled_jobs_device_id", "device_id"),
        Index("ix_scheduled_jobs_routine_id", "routine_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    scheduled_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    recurrence_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=lambda: {"kind": "once"}
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="scheduled")
    missed_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="notify_missed"
    )
    next_run_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_id: Mapped[str | None] = mapped_column(
        String(96),
        ForeignKey("smart_home_devices.id", ondelete="SET NULL"),
        nullable=True,
    )
    routine_id: Mapped[str | None] = mapped_column(
        String(48),
        ForeignKey("smart_home_routines.id", ondelete="SET NULL"),
        nullable=True,
    )
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


class JobExecution(Base):
    """Auditable per-occurrence execution log with unique occurrence deduplication."""

    __tablename__ = "job_executions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('due', 'running', 'completed', 'partial', 'failed', 'missed', 'cancelled')",
            name="ck_job_executions_status_enum",
        ),
        UniqueConstraint(
            "job_id",
            "scheduled_occurrence_utc",
            name="uq_job_executions_job_occurrence",
        ),
        Index("ix_job_executions_job_started", "job_id", "started_at_utc"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("scheduled_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    scheduled_occurrence_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    started_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )
    finished_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class NotificationRecord(Base):
    """Persistent local notification persisted before WS dispatch."""

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "language IN ('en', 'hi', 'kn')",
            name="ck_notifications_lang_enum",
        ),
        CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name="ck_notifications_priority_enum",
        ),
        Index(
            "uq_notifications_dedup_key",
            "dedup_key",
            unique=True,
            postgresql_where=text("dedup_key IS NOT NULL"),
        ),
        Index("ix_notifications_unread", "is_dismissed", "is_read", "created_at_utc"),
        Index("ix_notifications_type_created", "notification_type", "created_at_utc"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    notification_type: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_dismissed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    spoken_delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dedup_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )
    read_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class NotificationPreference(Base):
    """Persistent visual, spoken, and quiet-hours notification preferences."""

    __tablename__ = "notification_preferences"

    profile_id: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    visual_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    spoken_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quiet_hours_start: Mapped[str] = mapped_column(String(5), nullable=False, default="22:00")
    quiet_hours_end: Mapped[str] = mapped_column(String(5), nullable=False, default="07:00")
    timezone_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    updated_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
        onupdate=_utc_now,
    )
