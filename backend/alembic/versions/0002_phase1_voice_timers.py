"""Phase 1 persistent timers and voice turn metrics tables.

Revision ID: 0002_phase1_voice_timers
Revises: 0001_phase0_foundation
Create Date: 2026-11-25 15:22:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0002_phase1_voice_timers"
down_revision: str | None = "0001_phase0_foundation"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "timers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("label", sa.String(length=128), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("remaining_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("fire_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "duration_seconds > 0 AND duration_seconds <= 86400",
            name="ck_timers_duration_range",
        ),
        sa.CheckConstraint(
            "remaining_seconds >= 0 AND remaining_seconds <= duration_seconds",
            name="ck_timers_remaining_range",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'completed', 'cancelled', 'missed')",
            name="ck_timers_status_enum",
        ),
    )
    op.create_index("ix_timers_status_fire_at", "timers", ["status", "fire_at_utc"])

    op.create_table(
        "voice_turn_metrics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("requested_compute_mode", sa.String(length=16), nullable=False),
        sa.Column("effective_compute_mode", sa.String(length=16), nullable=False),
        sa.Column("intent_name", sa.String(length=64), nullable=False),
        sa.Column("intent_source", sa.String(length=32), nullable=False),
        sa.Column("final_state", sa.String(length=24), nullable=False),
        sa.Column("stt_ms", sa.Float(), nullable=True),
        sa.Column("router_ms", sa.Float(), nullable=True),
        sa.Column("llm_ms", sa.Float(), nullable=True),
        sa.Column("tool_ms", sa.Float(), nullable=True),
        sa.Column("tts_ms", sa.Float(), nullable=True),
        sa.Column("ttfa_ms", sa.Float(), nullable=True),
        sa.Column("e2e_ms", sa.Float(), nullable=True),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_voice_turn_metrics_turn_id", "voice_turn_metrics", ["turn_id"])


def downgrade() -> None:
    op.drop_index("ix_voice_turn_metrics_turn_id", table_name="voice_turn_metrics")
    op.drop_table("voice_turn_metrics")
    op.drop_index("ix_timers_status_fire_at", table_name="timers")
    op.drop_table("timers")
