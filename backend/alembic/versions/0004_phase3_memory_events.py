"""Phase 3 persistent personal memory, job scheduler, routines, and notifications.

Revision ID: 0004_phase3_memory_events
Revises: 0003_phase2_smart_home
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_phase3_memory_events"
down_revision: str | None = "0003_phase2_smart_home"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # 1. Extend smart_home_routines for custom routines, triggers, and conditions
    op.add_column("smart_home_routines", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "smart_home_routines",
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "smart_home_routines",
        sa.Column(
            "triggers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[{" + '"type":"manual"' + "}]'::jsonb"),
        ),
    )
    op.add_column(
        "smart_home_routines",
        sa.Column(
            "conditions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "smart_home_routines",
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "smart_home_routines",
        sa.Column(
            "updated_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.execute(
        "UPDATE smart_home_routines SET is_builtin = true WHERE id IN ('morning', 'night', 'study', 'away')"
    )

    # 2. Memory settings & persistent memory items
    op.create_table(
        "memory_settings",
        sa.Column("profile_id", sa.String(64), primary_key=True),
        sa.Column("memory_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_save_preferences", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "persist_conversation_history",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("semantic_search_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "embedding_model",
            sa.String(64),
            nullable=False,
            server_default="nomic-embed-text:latest",
        ),
        sa.Column(
            "updated_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "retention_days IS NULL OR retention_days > 0",
            name="ck_memory_settings_retention_positive",
        ),
    )

    op.create_table(
        "memory_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("profile_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("category", sa.String(32), nullable=False, server_default="preference"),
        sa.Column("canonical_key", sa.String(160), nullable=True),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column(
            "structured_value",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("provenance", sa.String(32), nullable=False, server_default="explicit_text"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column("embedding_model", sa.String(64), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "category IN ('appliance_preference', 'preference', 'fact', 'instruction', 'profile')",
            name="ck_memory_items_category_enum",
        ),
        sa.CheckConstraint("language IN ('en', 'hi', 'kn')", name="ck_memory_items_language_enum"),
        sa.CheckConstraint(
            "provenance IN ('explicit_voice', 'explicit_text', 'ui_edit', 'api', 'imported')",
            name="ck_memory_items_provenance_enum",
        ),
    )
    op.create_index(
        "uq_memory_items_profile_canonical_key",
        "memory_items",
        ["profile_id", "canonical_key"],
        unique=True,
        postgresql_where=sa.text("canonical_key IS NOT NULL"),
    )
    op.create_index(
        "ix_memory_items_profile_cat_enabled",
        "memory_items",
        ["profile_id", "category", "enabled"],
    )
    op.execute(
        "CREATE INDEX ix_memory_items_fts_simple ON memory_items USING gin (to_tsvector('simple', original_text))"
    )

    op.create_table(
        "conversation_history_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("profile_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("turn_id", sa.String(64), nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("intent", sa.String(64), nullable=False),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_conv_history_role_enum"),
        sa.CheckConstraint("language IN ('en', 'hi', 'kn')", name="ck_conv_history_lang_enum"),
    )
    op.create_index(
        "ix_conv_history_profile_created",
        "conversation_history_items",
        ["profile_id", "created_at_utc"],
    )

    # 3. Persistent scheduler & execution history
    op.create_table(
        "scheduled_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column(
            "payload_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("scheduled_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone_name", sa.String(64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column(
            "recurrence_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{" + '"kind":"once"' + "}'::jsonb"),
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(24), nullable=False, server_default="scheduled"),
        sa.Column("missed_policy", sa.String(32), nullable=False, server_default="notify_missed"),
        sa.Column("next_run_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "device_id",
            sa.String(96),
            sa.ForeignKey("smart_home_devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "routine_id",
            sa.String(48),
            sa.ForeignKey("smart_home_routines.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "job_type IN ('reminder', 'device_action', 'routine')",
            name="ck_scheduled_jobs_type_enum",
        ),
        sa.CheckConstraint("language IN ('en', 'hi', 'kn')", name="ck_scheduled_jobs_lang_enum"),
        sa.CheckConstraint(
            "status IN ('scheduled', 'due', 'running', 'completed', 'failed', 'missed', 'cancelled', 'paused')",
            name="ck_scheduled_jobs_status_enum",
        ),
        sa.CheckConstraint(
            "missed_policy IN ('notify_missed', 'skip', 'execute_if_safe')",
            name="ck_scheduled_jobs_missed_policy_enum",
        ),
    )
    op.create_index(
        "ix_scheduled_jobs_due", "scheduled_jobs", ["enabled", "status", "next_run_at_utc"]
    )
    op.create_index("ix_scheduled_jobs_type_status", "scheduled_jobs", ["job_type", "status"])
    op.create_index("ix_scheduled_jobs_device_id", "scheduled_jobs", ["device_id"])
    op.create_index("ix_scheduled_jobs_routine_id", "scheduled_jobs", ["routine_id"])

    op.create_table(
        "job_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("scheduled_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scheduled_occurrence_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="running"),
        sa.Column(
            "started_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", postgresql.JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.CheckConstraint(
            "status IN ('due', 'running', 'completed', 'partial', 'failed', 'missed', 'cancelled')",
            name="ck_job_executions_status_enum",
        ),
        sa.UniqueConstraint(
            "job_id", "scheduled_occurrence_utc", name="uq_job_executions_job_occurrence"
        ),
    )
    op.create_index("ix_job_executions_job_started", "job_executions", ["job_id", "started_at_utc"])

    # 4. Proactive persistent notifications & preferences
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("notification_type", sa.String(48), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("source_id", sa.String(128), nullable=True),
        sa.Column("priority", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_dismissed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("spoken_delivered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dedup_key", sa.String(160), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("read_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("language IN ('en', 'hi', 'kn')", name="ck_notifications_lang_enum"),
        sa.CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name="ck_notifications_priority_enum",
        ),
    )
    op.create_index(
        "uq_notifications_dedup_key",
        "notifications",
        ["dedup_key"],
        unique=True,
        postgresql_where=sa.text("dedup_key IS NOT NULL"),
    )
    op.create_index(
        "ix_notifications_unread",
        "notifications",
        ["is_dismissed", "is_read", "created_at_utc"],
    )
    op.create_index(
        "ix_notifications_type_created", "notifications", ["notification_type", "created_at_utc"]
    )

    op.create_table(
        "notification_preferences",
        sa.Column("profile_id", sa.String(64), primary_key=True),
        sa.Column("visual_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("spoken_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("quiet_hours_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("quiet_hours_start", sa.String(5), nullable=False, server_default="22:00"),
        sa.Column("quiet_hours_end", sa.String(5), nullable=False, server_default="07:00"),
        sa.Column("timezone_name", sa.String(64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column(
            "updated_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("notification_preferences")
    op.drop_index("ix_notifications_type_created", table_name="notifications")
    op.drop_index("ix_notifications_unread", table_name="notifications")
    op.drop_index("uq_notifications_dedup_key", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index("ix_job_executions_job_started", table_name="job_executions")
    op.drop_table("job_executions")

    op.drop_index("ix_scheduled_jobs_routine_id", table_name="scheduled_jobs")
    op.drop_index("ix_scheduled_jobs_device_id", table_name="scheduled_jobs")
    op.drop_index("ix_scheduled_jobs_type_status", table_name="scheduled_jobs")
    op.drop_index("ix_scheduled_jobs_due", table_name="scheduled_jobs")
    op.drop_table("scheduled_jobs")

    op.drop_index("ix_conv_history_profile_created", table_name="conversation_history_items")
    op.drop_table("conversation_history_items")

    op.execute("DROP INDEX IF EXISTS ix_memory_items_fts_simple")
    op.drop_index("ix_memory_items_profile_cat_enabled", table_name="memory_items")
    op.drop_index("uq_memory_items_profile_canonical_key", table_name="memory_items")
    op.drop_table("memory_items")
    op.drop_table("memory_settings")

    op.drop_column("smart_home_routines", "updated_at_utc")
    op.drop_column("smart_home_routines", "created_at_utc")
    op.drop_column("smart_home_routines", "conditions")
    op.drop_column("smart_home_routines", "triggers")
    op.drop_column("smart_home_routines", "is_builtin")
    op.drop_column("smart_home_routines", "description")
