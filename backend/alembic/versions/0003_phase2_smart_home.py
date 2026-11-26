"""Phase 2 persistent smart-home simulator foundation.

Revision ID: 0003_phase2_smart_home
Revises: 0002_phase1_voice_timers
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_phase2_smart_home"
down_revision: str | None = "0002_phase1_voice_timers"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "smart_home_devices",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("room", sa.String(48), nullable=False),
        sa.Column("device_type", sa.String(48), nullable=False),
        sa.Column("name", sa.String(96), nullable=False),
        sa.Column("aliases", postgresql.JSONB(), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(24), nullable=False, server_default="simulated"),
        sa.Column("is_essential", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("power_rating_w", sa.Float(), nullable=True),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_smart_home_devices_room", "smart_home_devices", ["room"])
    op.create_index("ix_smart_home_devices_device_type", "smart_home_devices", ["device_type"])
    op.create_table(
        "smart_home_state_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("device_id", sa.String(96), nullable=False),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("old_state", postgresql.JSONB(), nullable=False),
        sa.Column("new_state", postgresql.JSONB(), nullable=False),
        sa.Column("context_id", sa.String(64), nullable=False),
        sa.Column("context_source", sa.String(24), nullable=False),
        sa.Column("recorded_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["device_id"], ["smart_home_devices.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_smart_home_state_history_device_id", "smart_home_state_history", ["device_id"])
    op.create_table(
        "smart_home_routines",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("name", sa.String(96), nullable=False),
        sa.Column("aliases", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("actions", postgresql.JSONB(), nullable=False),
        sa.Column("last_run_at_utc", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS smart_home_routines")
    op.drop_index("ix_smart_home_state_history_device_id", table_name="smart_home_state_history")
    op.drop_table("smart_home_state_history")
    op.drop_index("ix_smart_home_devices_device_type", table_name="smart_home_devices")
    op.drop_index("ix_smart_home_devices_room", table_name="smart_home_devices")
    op.drop_table("smart_home_devices")
